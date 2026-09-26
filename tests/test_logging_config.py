import json
import logging
import uuid

from assay.config import settings
from assay.logging_config import (
    JsonFormatter,
    TextFormatter,
    _record_factory,
    configure_logging,
    request_id_var,
    run_id_var,
)


def _record(message="hello", level=logging.INFO, extra=None, exc_info=None):
    record = _record_factory("assay.test", level, __file__, 1, message, (), exc_info)
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def _exc_info():
    try:
        raise ValueError("boom")
    except ValueError as exc:
        return (type(exc), exc, exc.__traceback__)


# --- record factory ---


def test_record_factory_defaults_request_id_to_a_dash_outside_a_request():
    assert _record().request_id == "-"


def test_record_factory_reads_the_request_id_from_the_context():
    token = request_id_var.set("req-123")
    try:
        assert _record().request_id == "req-123"
    finally:
        request_id_var.reset(token)


def test_record_factory_carries_the_run_id_only_while_one_is_set():
    assert not hasattr(_record(), "run_id")
    token = run_id_var.set("run-9")
    try:
        assert _record().run_id == "run-9"
    finally:
        run_id_var.reset(token)


def test_run_id_is_an_ordinary_extra_outside_a_task(caplog):
    # the API's run-creation lines pass run_id in extra= — must not collide
    # with the factory (logging refuses to overwrite a stamped attribute)
    configure_logging(settings.log_level, settings.log_format)
    with caplog.at_level(logging.INFO, logger="assay.test"):
        logging.getLogger("assay.test").info("created", extra={"run_id": "run-api"})

    record = caplog.records[-1]
    assert json.loads(JsonFormatter().format(record))["run_id"] == "run-api"


# --- JsonFormatter ---


def test_json_formatter_emits_fixed_fields_then_extras():
    dataset_id = uuid.uuid4()
    line = JsonFormatter().format(_record(extra={"dataset_id": dataset_id, "row_count": 3}))

    payload = json.loads(line)
    assert payload["timestamp"].endswith("+00:00")
    assert payload["level"] == "INFO"
    assert payload["logger"] == "assay.test"
    assert payload["message"] == "hello"
    assert payload["request_id"] == "-"
    assert payload["dataset_id"] == str(dataset_id)  # non-JSON types fall back to str()
    assert payload["row_count"] == 3


def test_json_formatter_does_not_leak_log_record_internals():
    payload = json.loads(JsonFormatter().format(_record()))

    assert {"args", "msg", "levelno", "pathname", "exc_info"}.isdisjoint(payload)


def test_json_formatter_emits_run_id_only_when_set():
    assert "run_id" not in json.loads(JsonFormatter().format(_record()))

    token = run_id_var.set("run-9")
    try:
        record = _record()
    finally:
        run_id_var.reset(token)

    assert json.loads(JsonFormatter().format(record))["run_id"] == "run-9"


def test_json_formatter_includes_the_traceback():
    payload = json.loads(JsonFormatter().format(_record(exc_info=_exc_info())))

    assert "ValueError: boom" in payload["exception"]
    assert "Traceback" in payload["exception"]


# --- TextFormatter ---


def test_text_formatter_one_line_with_request_id_and_no_extras():
    token = request_id_var.set("req-123")
    try:
        record = _record(extra={"dataset_id": "not-shown"})
    finally:
        request_id_var.reset(token)

    line = TextFormatter().format(record)

    assert line.endswith(" INFO [req-123] assay.test: hello")
    assert "not-shown" not in line


def test_text_formatter_appends_the_traceback():
    line = TextFormatter().format(_record(exc_info=_exc_info()))

    assert line.startswith
    assert "\nTraceback" in line
    assert line.rstrip().endswith("ValueError: boom")


def test_text_formatter_tolerates_a_record_without_request_id():
    plain = logging.LogRecord("x", logging.INFO, __file__, 1, "hi", (), None)
    if hasattr(plain, "request_id"):  # not built by our factory in this process
        del plain.request_id

    assert " INFO [-] x: hi" in TextFormatter().format(plain)


# --- configure_logging ---


def _restore():
    configure_logging(settings.log_level, settings.log_format)


def test_configure_logging_scopes_the_level_to_the_app_namespace():
    try:
        configure_logging("DEBUG", "text")

        assert logging.getLogger("assay").level == logging.DEBUG
        assert logging.getLogger().level == logging.INFO  # third-party stays at INFO
        assert logging.getLogger("sqlalchemy.engine").level == logging.INFO  # SQL statements on
        assert logging.getLogger("uvicorn.access").level == logging.WARNING  # ours replaces it

        configure_logging("WARNING", "text")

        assert logging.getLogger("assay").level == logging.WARNING
        assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING  # SQL off again
    finally:
        _restore()


def test_configure_logging_picks_the_formatter():
    try:
        configure_logging("INFO", "json")
        assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)

        configure_logging("INFO", "text")
        assert isinstance(logging.getLogger().handlers[0].formatter, TextFormatter)
    finally:
        _restore()


def test_configure_logging_is_a_no_op_for_identical_arguments():
    try:
        configure_logging("WARNING", "json")
        handler = logging.getLogger().handlers[0]

        configure_logging("WARNING", "json")

        assert logging.getLogger().handlers[0] is handler
    finally:
        _restore()
