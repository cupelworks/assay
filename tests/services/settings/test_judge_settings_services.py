import asyncio
import logging
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from kombu.exceptions import OperationalError

from assay.config import settings
from assay.models import JudgeCheckModel, SettingsModel, SettingsSection, TargetCheckStatus
from assay.schemas import JudgeCheckRequest, JudgeProvider, JudgeSettingsUpdate, SettingsSource
from assay.services import (
    create_judge_check,
    get_judge_check,
    get_judge_settings,
    reset_judge_settings,
    update_judge_settings,
)

SAVED_AT = datetime(2026, 10, 1, 9, 30)
_PATCH_SEND = "assay.services.settings.create_judge_check._celery_app.send_task"


def _session(row=None) -> AsyncMock:
    session = AsyncMock()
    session.get.return_value = row
    session.add = lambda obj: session.added.append(obj)
    session.added = []
    return session


def _saved_row(**value) -> SettingsModel:
    return SettingsModel(section=SettingsSection.judge, updated_at=SAVED_AT,
                         value={"provider": "anthropic", "model": "claude-sonnet-5-5", **value})


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setattr(settings, "judge_provider", None)
    monkeypatch.setattr(settings, "judge_model", None)


# --- GET ---


def test_get_reads_the_environment_when_nothing_is_saved():
    session = _session()

    result = asyncio.run(get_judge_settings(session))

    session.get.assert_awaited_once_with(SettingsModel, SettingsSection.judge)
    assert (result.source, result.provider, result.api_key_variable) == (
        SettingsSource.environment, None, None)


def test_get_reads_the_saved_row_with_the_variable_its_key_is_read_from():
    result = asyncio.run(get_judge_settings(_session(_saved_row())))

    assert (result.source, result.updated_at) == (SettingsSource.database, SAVED_AT)
    assert (result.model, result.api_key_variable) == ("claude-sonnet-5-5", "ANTHROPIC_API_KEY")


# --- PATCH ---


def test_the_first_save_carries_the_environment_forward_with_the_changes_applied(monkeypatch):
    monkeypatch.setattr(settings, "judge_timeout_seconds", 30)
    session = _session()

    result = asyncio.run(update_judge_settings(
        JudgeSettingsUpdate(provider="openai", model="gpt-4o-mini"), session))

    (row,) = session.added
    assert row.section == SettingsSection.judge
    assert row.value["provider"] == "openai"
    assert row.value["timeout_seconds"] == 30  # the environment's, carried forward
    assert (result.source, result.api_key_variable) == (SettingsSource.database,
                                                        "OPENAI_API_KEY")
    session.commit.assert_awaited_once()


def test_a_later_save_updates_the_row_in_place():
    row = _saved_row()
    session = _session(row)

    asyncio.run(update_judge_settings(JudgeSettingsUpdate(model="claude-haiku-4-5-20251001"),
                                      session))

    assert session.added == []
    assert row.value["model"] == "claude-haiku-4-5-20251001"
    assert row.value["provider"] == "anthropic"
    assert row.updated_at != SAVED_AT


def test_provider_null_turns_the_judge_off():
    row = _saved_row()

    result = asyncio.run(update_judge_settings(JudgeSettingsUpdate(provider=None), _session(row)))

    assert result.provider is None
    assert result.api_key_variable is None


def test_api_key_env_null_goes_back_to_the_providers_standard_variable():
    row = _saved_row(api_key_env="TEAM_KEY")

    result = asyncio.run(update_judge_settings(JudgeSettingsUpdate(api_key_env=None),
                                               _session(row)))

    assert result.api_key_variable == "ANTHROPIC_API_KEY"


def test_invalid_results_are_a_422_listing_every_problem_and_nothing_is_saved():
    session = _session()

    with pytest.raises(RequestValidationError) as caught:
        asyncio.run(update_judge_settings(
            JudgeSettingsUpdate(provider=JudgeProvider.anthropic, api_key_env="1KEY"), session))

    assert {tuple(error["loc"]) for error in caught.value.errors()} == {
        ("body", "model"), ("body", "api_key_env")}
    assert all("input" not in error for error in caught.value.errors())
    assert session.added == []
    session.commit.assert_not_awaited()


def test_a_save_logs_the_field_names_never_the_values(caplog):
    with caplog.at_level(logging.INFO, logger="assay.services.settings.update_judge_settings"):
        asyncio.run(update_judge_settings(
            JudgeSettingsUpdate(provider="openai", model="secret-model-name"), _session()))

    assert "Saved the judge settings (first save): model, provider" in caplog.text
    assert "secret-model-name" not in caplog.text


# --- DELETE ---


def test_reset_deletes_the_saved_row_and_returns_the_environments_settings():
    row = _saved_row()
    session = _session(row)

    result = asyncio.run(reset_judge_settings(session))

    session.delete.assert_awaited_once_with(row)
    assert (result.source, result.provider) == (SettingsSource.environment, None)


def test_resetting_with_nothing_saved_is_not_an_error():
    session = _session()

    result = asyncio.run(reset_judge_settings(session))

    session.delete.assert_not_awaited()
    assert result.source == SettingsSource.environment


# --- checks ---


def test_a_check_stores_the_settings_in_effect_and_is_sent_to_a_worker():
    session = _session(_saved_row())

    with patch(_PATCH_SEND) as send:
        check = asyncio.run(create_judge_check(JudgeCheckRequest(), session))

    (row,) = session.added
    assert row.status == TargetCheckStatus.pending
    assert row.settings["model"] == "claude-sonnet-5-5"
    assert send.call_args.args == ("assay.worker.tasks.check_judge.check_judge",)
    assert send.call_args.kwargs["args"] == [row.id]
    assert (check.id, check.status, check.ok) == (row.id, TargetCheckStatus.pending, None)


def test_proposed_settings_are_checked_but_never_saved():
    row = _saved_row()
    session = _session(row)

    with patch(_PATCH_SEND):
        check = asyncio.run(create_judge_check(
            JudgeCheckRequest(settings=JudgeSettingsUpdate(model="claude-haiku-4-5-20251001")),
            session))

    assert check.settings.model == "claude-haiku-4-5-20251001"
    assert row.value["model"] == "claude-sonnet-5-5"


def test_invalid_proposed_settings_are_a_422_pointing_inside_settings_and_no_check():
    session = _session()

    with pytest.raises(RequestValidationError) as caught:
        asyncio.run(create_judge_check(
            JudgeCheckRequest(settings=JudgeSettingsUpdate(provider="anthropic")), session))

    assert [error["loc"] for error in caught.value.errors()] == [("body", "settings", "model")]
    assert session.added == []


def test_a_check_that_cannot_be_sent_completes_at_once_with_the_reason():
    session = _session(_saved_row())

    with patch(_PATCH_SEND, side_effect=OperationalError("no broker")):
        check = asyncio.run(create_judge_check(JudgeCheckRequest(), session))

    assert (check.status, check.ok, check.error) == (
        TargetCheckStatus.completed, False, "Could not be sent to a worker")


def test_reading_a_check_returns_it_as_far_as_it_has_got():
    row = JudgeCheckModel(id=uuid.uuid4(), created_at=SAVED_AT,
                          status=TargetCheckStatus.completed, ok=True, status_code=200,
                          latency_ms=812.0, answer="It answers the question.",
                          settings={"provider": "anthropic", "model": "claude-sonnet-5-5"})
    session = _session(row)

    check = asyncio.run(get_judge_check(row.id, session))

    session.get.assert_awaited_once_with(JudgeCheckModel, row.id)
    assert (check.ok, check.answer, check.settings.model) == (
        True, "It answers the question.", "claude-sonnet-5-5")


def test_an_unknown_check_is_a_404():
    check_id = uuid.uuid4()

    with pytest.raises(HTTPException) as caught:
        asyncio.run(get_judge_check(check_id, _session()))

    assert caught.value.status_code == 404
    assert caught.value.detail == f"Check with ID '{check_id}' not found"


def test_get_returns_the_endpoint_the_worker_calls():
    default = asyncio.run(get_judge_settings(_session(_saved_row())))
    written = asyncio.run(get_judge_settings(_session(_saved_row(
        provider="openai", url="http://localhost:11434/v1/chat/completions"))))
    no_judge = asyncio.run(get_judge_settings(_session()))

    assert (default.url, default.endpoint) == (None, "https://api.anthropic.com/v1/messages")
    assert written.endpoint == "http://localhost:11434/v1/chat/completions"
    assert no_judge.endpoint is None
