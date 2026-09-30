"""Logging setup, applied once per process: by create_app() in the API, and
by Celery's setup_logging signal in the worker and Beat (assay/worker/
celery_app.py), so all three emit the same records.

Everything goes to stdout, one record per line — the container/platform
collects it from there (twelve-factor). Two formats, picked by
ASSAY_LOG_FORMAT: `text` for a terminal, `json` for a log collector.

Every record carries the request ID of the request being handled (set by
assay.middleware.RequestContextMiddleware in the API; in the worker, by the
task wrapper from the ID the API put in the task message), injected through
the log record factory so it's there for any handler — ours, pytest's
caplog, a future exporter — not only for handlers that happen to carry a
filter. Outside a request (startup, a run the reconciliation scan
re-published) it's "-". In the worker, records also carry the ID of the run
being executed, as `run_id`.

Convention for call sites: the message is self-contained and readable on
its own; the same values go in `extra=` as structured fields for the JSON
format. The text format shows the message only.
"""
import json
import logging
import logging.config
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Literal

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)

# Attributes every LogRecord has by construction — anything else on a record
# came from `extra=` and is a structured field worth emitting. request_id and
# run_id are emitted explicitly by the JSON formatter, so they're excluded here.
_STANDARD_ATTRS = frozenset(
    {*vars(logging.LogRecord("", 0, "", 0, "", (), None)), "message", "asctime",
     "request_id", "run_id"}
)
_BASE_RECORD_FACTORY = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs) -> logging.LogRecord:
    """logging refuses to let `extra=` overwrite an attribute the record
    already has, so what's stamped here is reserved at the call sites:
    request_id always (never pass it in `extra=`); run_id only while a task
    is executing a run (never pass it in the worker's task code — outside
    that, e.g. the API's run-creation lines, it's an ordinary field).
    """
    record = _BASE_RECORD_FACTORY(*args, **kwargs)
    record.request_id = request_id_var.get() or "-"
    run_id = run_id_var.get()
    if run_id is not None:
        record.run_id = run_id
    return record


def _extras(record: logging.LogRecord) -> dict:
    return {key: value for key, value in vars(record).items() if key not in _STANDARD_ATTRS}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: fixed fields first, then whatever was passed
    as `extra=`, then the formatted traceback when there is one.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if getattr(record, "run_id", None) is not None:
            payload["run_id"] = record.run_id
        payload.update(_extras(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """`2026-09-26 15:23:46,113 INFO [<request_id>] assay.access: GET /runs -> 200 (3.1 ms)`,
    followed by the traceback on its own lines when there is one.
    """

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        # A record built by another factory (e.g. one rebuilt from a pickle)
        # has no request_id — don't let the format string fail on it.
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return super().format(record)


_configured: tuple[str, str] | None = None


def configure_logging(level: str = "INFO", fmt: Literal["text", "json"] = "text") -> None:
    """Configure process-wide logging — the API's, and the worker's/Beat's
    through Celery's setup_logging signal. Idempotent for identical
    arguments, so creating the app twice (tests do) doesn't rebuild the
    handlers — which would also discard any handler someone else attached to
    the root logger in between, such as pytest's log capture.

    Args:
        level: Level for the `assay` logger namespace. DEBUG also turns on
            SQL statement logging (sqlalchemy.engine at INFO — the same
            output as the engine's echo=True, without echo's own extra
            stdout handler that would print every statement twice).
        fmt: `text` or `json`, see the module docstring.
    """
    global _configured
    if _configured == (level, fmt):
        return

    logging.setLogRecordFactory(_record_factory)
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "text": {"()": TextFormatter},
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": sys.stdout,
                    "formatter": fmt,
                },
            },
            # Third-party loggers stay at INFO whatever the app's level is —
            # DEBUG on the root would drown the app's own output in library
            # internals (aiosqlite logs every cursor operation, for one).
            "root": {"level": "INFO", "handlers": ["stdout"]},
            "loggers": {
                "assay": {"level": level},
                "sqlalchemy.engine": {"level": "INFO" if level == "DEBUG" else "WARNING"},
                # uvicorn installs its own handlers before it imports the app;
                # take them over so server logs (startup, shutdown, and the
                # traceback of any unhandled exception) share the format and
                # carry the request ID.
                "uvicorn": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"level": "INFO"},
                # Replaced by RequestContextMiddleware's access line, which
                # adds the request ID, the route template and the duration.
                "uvicorn.access": {"handlers": ["stdout"], "level": "WARNING", "propagate": False},
                # rouge-score logs "Using default tokenizer." through absl on
                # every ROUGE check.
                "absl": {"level": "WARNING"},
            },
        }
    )
    _configured = (level, fmt)
