"""Celery application for the run-execution worker.

The Celery app itself, configured against Redis (or the local SQLite
alternative) as broker and result backend, imported from the same
settings the API process already uses. Two tasks are registered:
`execute_run` (tasks/execute_run.py — runs one TestRunModel end to end; the
deterministic engines score for real, the metric and judge engines are
still stubs) and `reconcile_runs`
(tasks/reconcile_runs.py — the Beat-scheduled safety net that re-publishes
Pending runs whose original dispatch was lost).

The API process imports this app only to publish tasks by name
(services/runs/_common.py's _dispatch_runs); it never imports the task or
evaluator modules, and this never imports assay.main. Run with:

    celery -A assay.worker worker --loglevel=info
    celery -A assay.worker beat --loglevel=info   # exactly one per environment

Logging is the API's (assay/logging_config.py), applied through Celery's
setup_logging signal below: same formats, ASSAY_LOG_LEVEL/ASSAY_LOG_FORMAT
apply here too, and each run's lines carry the request ID of the API call
that created it. The CLI's --loglevel keeps governing Celery's own loggers.
"""
import logging
import ssl
from datetime import timedelta
from urllib.parse import urlparse

from celery import Celery
from celery.signals import beat_init, setup_logging, worker_ready
from kombu.utils.url import maybe_sanitize_url

from assay import __version__
from assay.config import settings
from assay.logging_config import configure_logging

logger = logging.getLogger(__name__)


def _use_ssl_if_rediss(url: str) -> dict | None:
    """kombu's redis transport recognizes a rediss:// URL and switches to TLS
    on its own, but its default ssl_cert_reqs is CERT_NONE — TLS with no
    certificate verification, which defeats most of the point (e.g. a
    managed broker like Azure Cache for Redis, which requires rediss:// by
    default). Returns config forcing verification for a rediss:// URL, or
    None for a plain redis:// one (unauthenticated local dev, left as-is).
    """
    if urlparse(url).scheme != "rediss":
        return None
    return {"ssl_cert_reqs": ssl.CERT_REQUIRED}


app = Celery(
    "assay",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    # Every task module the worker needs to import to register its tasks —
    # a task only exists on `app` once its @app.task()-decorated module has
    # actually been imported. New task modules join this list, not just
    # tasks/__init__.py's own __all__ (that re-export is for ergonomic
    # access to assay.worker.tasks.execute_run, it doesn't by itself make
    # Celery import anything at worker startup).
    include=["assay.worker.tasks.execute_run", "assay.worker.tasks.reconcile_runs"],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_use_ssl=_use_ssl_if_rediss(settings.celery_broker_url),
    redis_backend_use_ssl=_use_ssl_if_rediss(settings.celery_result_backend),
    # No task's return value is ever read back — outcomes live in the database.
    # Ignoring results also keeps .delay() from touching the result store before
    # publishing, so a publish can only fail with a KombuError (broker/encoding),
    # never the result store's generic RuntimeError. Note: app.send_task does NOT
    # read this setting — callers publishing by name must pass ignore_result=True.
    task_ignore_result=True,
    # Only read by a Beat process (celery -A assay.worker beat) — exactly one per
    # environment, or every tick gets published twice. The workers themselves
    # execute reconcile_runs like any other task.
    beat_schedule={
        "reconcile-runs": {
            "task": "assay.worker.tasks.reconcile_runs.reconcile_runs",
            "schedule": timedelta(minutes=settings.reconciliation_interval_minutes),
        },
    },
)


@setup_logging.connect
def _configure_logging(loglevel: int | None = None, **_kwargs) -> None:
    """Celery skips its own logging setup (root-logger takeover, its own
    format) entirely when this signal has a receiver. ASSAY_LOG_LEVEL governs
    the `assay.*` loggers here exactly as in the API; the CLI's --loglevel is
    applied to Celery's own loggers so it keeps meaning (task received/
    succeeded lines, Beat ticks).
    """
    configure_logging(settings.log_level, settings.log_format)
    if loglevel is not None:
        logging.getLogger("celery").setLevel(loglevel)


@worker_ready.connect
def _log_worker_ready(sender=None, **_kwargs) -> None:
    controller = getattr(sender, "controller", None)
    pool = getattr(getattr(controller, "pool_cls", None), "__module__", "?").rsplit(".", 1)[-1]
    concurrency = getattr(controller, "concurrency", None)
    logger.info(
        "Assay worker %s ready: log_level=%s log_format=%s pool=%s concurrency=%s broker=%s",
        __version__, settings.log_level, settings.log_format, pool, concurrency,
        maybe_sanitize_url(settings.celery_broker_url),
        extra={"version": __version__, "pool": pool, "concurrency": concurrency},
    )


@beat_init.connect
def _log_beat_start(**_kwargs) -> None:
    # Read from the schedule rather than naming tasks here, so a new entry in
    # beat_schedule shows up without this having to know about it.
    schedule = {
        name: {"task": entry["task"], "every": str(entry["schedule"])}
        for name, entry in (app.conf.beat_schedule or {}).items()
    }
    logger.info(
        "Assay Beat %s starting with %d scheduled task(s): %s",
        __version__, len(schedule),
        "; ".join(f"{name} -> {e['task']} every {e['every']}" for name, e in schedule.items()),
        extra={"version": __version__, "schedule": schedule},
    )
