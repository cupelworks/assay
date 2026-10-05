# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from assay.models import JudgeCheckModel, TargetCheckStatus
from assay.worker.llm import JudgeError, Verdict
from assay.worker.services.check_judge import ANSWER, QUESTION, check_judge

_PATCH_ASK = "assay.worker.services.check_judge.llm.ask_for_verdict"
SETTINGS = {"provider": "anthropic", "model": "claude-sonnet-5-5", "max_retries": 2}


def _check(created_at=None) -> JudgeCheckModel:
    return JudgeCheckModel(
        id=uuid.uuid4(), created_at=created_at or datetime.now().astimezone(),
        status=TargetCheckStatus.running, settings=dict(SETTINGS),
    )


def _claimed(check: JudgeCheckModel) -> MagicMock:
    session = MagicMock()
    session.execute.return_value.rowcount = 1
    session.scalar.return_value = check
    return session


def test_an_unclaimed_check_does_nothing():
    session = MagicMock()
    session.execute.return_value.rowcount = 0

    with patch(_PATCH_ASK) as ask:
        check_judge(uuid.uuid4(), session)

    ask.assert_not_called()


def test_a_readable_verdict_completes_the_check_ok_with_the_rationale():
    check = _check()
    verdict = Verdict(passed=True, rationale="It answers the question.", status=200,
                      latency_ms=812.3)

    with patch(_PATCH_ASK, return_value=verdict):
        check_judge(check.id, _claimed(check))

    assert check.status == TargetCheckStatus.completed
    assert (check.ok, check.answer, check.error) == (True, "It answers the question.", None)
    assert (check.status_code, check.latency_ms) == (200, 812.3)


def test_a_failing_verdict_is_still_ok():
    check = _check()
    verdict = Verdict(passed=False, rationale="Off-topic.", status=200, latency_ms=1.0)

    with patch(_PATCH_ASK, return_value=verdict):
        check_judge(check.id, _claimed(check))

    assert (check.ok, check.answer) == (True, "Off-topic.")


def test_the_fixed_question_is_asked_once_with_the_checks_own_settings():
    check = _check()

    with patch(_PATCH_ASK, return_value=Verdict(True, "Ok.", 200, 1.0)) as ask:
        check_judge(check.id, _claimed(check))

    settings, _system, prompt = ask.call_args.args
    assert (settings.model, settings.max_retries) == ("claude-sonnet-5-5", 0)
    assert QUESTION in prompt and ANSWER in prompt


def test_a_judge_that_cannot_answer_completes_the_check_with_the_reason():
    check = _check()

    with patch(_PATCH_ASK, side_effect=JudgeError("Judge answered HTTP 401: invalid x-api-key",
                                                  status=401, latency_ms=88.0)):
        check_judge(check.id, _claimed(check))

    assert (check.ok, check.answer) == (False, None)
    assert check.error == "Judge answered HTTP 401: invalid x-api-key"
    assert (check.status_code, check.latency_ms) == (401, 88.0)


def test_a_stale_check_expires_without_asking_the_judge():
    check = _check(created_at=datetime.now().astimezone() - timedelta(minutes=6))

    with patch(_PATCH_ASK) as ask:
        check_judge(check.id, _claimed(check))

    ask.assert_not_called()
    assert (check.ok, check.error) == (False, "Expired before a worker picked it up")


# --- never stuck in running ---


def test_settings_this_code_cant_read_complete_the_check_with_the_reason():
    check = _check()
    check.settings = {**SETTINGS, "base_url": "http://old.example"}
    session = _claimed(check)

    with patch(_PATCH_ASK) as ask:
        check_judge(check.id, session)

    ask.assert_not_called()
    session.rollback.assert_called_once()
    assert (check.status, check.ok) == (TargetCheckStatus.completed, False)
    assert check.error == ("The check failed on the worker: base_url: Extra inputs are not "
                           "permitted")
    assert check.completed_at is not None


def test_an_unexpected_error_completes_the_check_with_it():
    check = _check()

    with patch(_PATCH_ASK, side_effect=RuntimeError("database is locked")):
        check_judge(check.id, _claimed(check))

    assert (check.status, check.ok) == (TargetCheckStatus.completed, False)
    assert check.error == "The check failed on the worker: database is locked"


def test_an_error_with_no_message_is_named_by_its_type():
    check = _check()

    with patch(_PATCH_ASK, side_effect=KeyError()):
        check_judge(check.id, _claimed(check))

    assert check.error == "The check failed on the worker: KeyError"
