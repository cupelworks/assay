import logging
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from assay.models import TargetCheckModel, TargetCheckStatus
from assay.worker.services.check_target import CHECK_EXPIRES_AFTER, check_target
from assay.worker.target import TargetError, TargetResponse

_PATCH_GET_ANSWER = "assay.worker.services.check_target.target.get_answer"
SETTINGS = {"url": "http://app.test/chat", "max_retries": 2, "timeout_seconds": 5}


def _check(created_at=None, **settings) -> TargetCheckModel:
    return TargetCheckModel(
        id=uuid.uuid4(), created_at=created_at or datetime.now().astimezone(),
        status=TargetCheckStatus.running, input="Hello?", settings={**SETTINGS, **settings},
    )


def _claimed(check: TargetCheckModel) -> MagicMock:
    session = MagicMock()
    session.execute.return_value.rowcount = 1
    session.scalar.return_value = check
    return session


def test_an_unclaimed_check_does_nothing():
    session = MagicMock()
    session.execute.return_value.rowcount = 0

    with patch(_PATCH_GET_ANSWER) as get_answer:
        check_target(uuid.uuid4(), session)

    # missing, or already taken by another worker (a duplicate delivery)
    get_answer.assert_not_called()
    session.scalar.assert_not_called()


def test_a_usable_answer_completes_the_check_ok():
    check = _check()
    reply = TargetResponse(answer="Hi!", status=200, latency_ms=412.5, attempts=1)

    with patch(_PATCH_GET_ANSWER, return_value=reply):
        check_target(check.id, _claimed(check))

    assert check.status == TargetCheckStatus.completed
    assert (check.ok, check.answer, check.error) == (True, "Hi!", None)
    assert (check.status_code, check.latency_ms) == (200, 412.5)
    assert check.completed_at is not None


def test_the_call_uses_the_checks_own_settings_with_no_retries():
    check = _check(output_path="$.reply")

    with patch(_PATCH_GET_ANSWER, return_value=TargetResponse("Hi!", 200, 1.0, 1)) as get_answer:
        check_target(check.id, _claimed(check))

    (input_text, settings), _ = get_answer.call_args
    assert input_text == "Hello?"
    assert (settings.url, settings.output_path) == ("http://app.test/chat", "$.reply")
    assert settings.max_retries == 0


def test_a_failed_call_completes_the_check_with_the_reason_and_what_came_back():
    check = _check()
    refused = TargetError("Application answered HTTP 401", status=401, latency_ms=88.1)

    with patch(_PATCH_GET_ANSWER, side_effect=refused):
        check_target(check.id, _claimed(check))

    assert check.status == TargetCheckStatus.completed
    assert (check.ok, check.answer) == (False, None)
    assert check.error == "Application answered HTTP 401"
    assert (check.status_code, check.latency_ms) == (401, 88.1)


def test_an_unset_header_variable_on_the_worker_is_reported_by_name():
    check = _check()
    missing = TargetError("A header references ${KEY} but KEY is not set on this server")

    with patch(_PATCH_GET_ANSWER, side_effect=missing):
        check_target(check.id, _claimed(check))

    assert check.error == "A header references ${KEY} but KEY is not set on this server"
    assert (check.status_code, check.latency_ms) == (None, None)


def test_a_check_queued_too_long_expires_without_calling_the_application():
    check = _check(created_at=datetime.now() - CHECK_EXPIRES_AFTER - timedelta(seconds=1))

    with patch(_PATCH_GET_ANSWER) as get_answer:
        check_target(check.id, _claimed(check))

    get_answer.assert_not_called()
    assert check.status == TargetCheckStatus.completed
    assert (check.ok, check.error) == (False, "Expired before a worker picked it up")


def test_completion_is_logged_without_the_input_or_the_answer(caplog):
    check = _check()
    check.input = "my secret prompt"

    with patch(_PATCH_GET_ANSWER, return_value=TargetResponse("secret answer", 200, 3.0, 1)), \
            caplog.at_level(logging.INFO, logger="assay.worker.services.check_target"):
        check_target(check.id, _claimed(check))

    (record,) = caplog.records
    assert record.getMessage() == f"Check {check.id} completed: ok (HTTP 200)"
    assert "secret" not in record.getMessage()


def test_a_failure_with_no_response_is_logged_as_such(caplog):
    check = _check()
    unreachable = TargetError("ConnectError calling the application")

    with patch(_PATCH_GET_ANSWER, side_effect=unreachable), \
            caplog.at_level(logging.INFO, logger="assay.worker.services.check_target"):
        check_target(check.id, _claimed(check))

    (record,) = caplog.records
    assert record.getMessage() == f"Check {check.id} completed: failed (no response)"
