import asyncio
import logging
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.exceptions import RequestValidationError

from assay.config import settings
from assay.models import SettingsModel, SettingsSection
from assay.schemas import SettingsSource, TargetSettingsUpdate
from assay.services import get_target_settings, reset_target_settings, update_target_settings

SAVED_AT = datetime(2026, 9, 27, 15, 10)


def _session(row=None) -> AsyncMock:
    session = AsyncMock()
    session.get.return_value = row
    session.add = lambda obj: session.added.append(obj)
    session.added = []
    return session


def _saved_row(**value) -> SettingsModel:
    return SettingsModel(section=SettingsSection.target,
                         value={"url": "https://saved.example.test/chat", **value},
                         updated_at=SAVED_AT)


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setattr(settings, "target_url", "http://env.example.test/chat")
    monkeypatch.setattr(settings, "target_headers", {"Authorization": "Bearer ${ENV_KEY}"})


# --- GET ---


def test_get_reads_the_environment_when_nothing_is_saved():
    session = _session()

    result = asyncio.run(get_target_settings(session))

    session.get.assert_awaited_once_with(SettingsModel, SettingsSection.target)
    assert (result.source, result.updated_at) == (SettingsSource.environment, None)
    assert result.url == "http://env.example.test/chat"


def test_get_returns_header_references_unexpanded(monkeypatch):
    monkeypatch.setenv("ENV_KEY", "s3cret")

    result = asyncio.run(get_target_settings(_session()))

    assert result.headers == {"Authorization": "Bearer ${ENV_KEY}"}


def test_get_reads_the_saved_row_when_there_is_one():
    result = asyncio.run(get_target_settings(_session(_saved_row())))

    assert (result.source, result.updated_at) == (SettingsSource.database, SAVED_AT)
    assert result.url == "https://saved.example.test/chat"


# --- PATCH ---


def test_the_first_save_carries_the_environment_forward_with_the_changes_applied():
    session = _session()

    result = asyncio.run(update_target_settings(TargetSettingsUpdate(timeout_seconds=30),
                                                session))

    (row,) = session.added
    assert row.section == SettingsSection.target
    assert row.value["url"] == "http://env.example.test/chat"
    assert row.value["headers"] == {"Authorization": "Bearer ${ENV_KEY}"}
    assert row.value["timeout_seconds"] == 30
    session.commit.assert_awaited_once()
    assert result.source == SettingsSource.database
    assert result.updated_at == row.updated_at


def test_a_later_save_updates_the_row_in_place():
    row = _saved_row(max_retries=2)
    session = _session(row)

    result = asyncio.run(update_target_settings(TargetSettingsUpdate(max_retries=0), session))

    assert session.added == []
    assert row.value["max_retries"] == 0
    assert row.value["url"] == "https://saved.example.test/chat"
    assert row.updated_at != SAVED_AT
    assert result.max_retries == 0


def test_url_null_unsets_the_url():
    row = _saved_row()

    result = asyncio.run(update_target_settings(TargetSettingsUpdate(url=None), _session(row)))

    assert row.value["url"] is None
    assert result.url is None


def test_fields_not_sent_are_left_alone():
    row = _saved_row(timeout_seconds=45)

    asyncio.run(update_target_settings(TargetSettingsUpdate(method="put"), _session(row)))

    assert (row.value["method"], row.value["timeout_seconds"]) == ("PUT", 45)


def test_headers_are_replaced_not_merged():
    row = _saved_row(headers={"Authorization": "Bearer ${KEY}", "X-Tenant": "acme"})

    asyncio.run(update_target_settings(TargetSettingsUpdate(headers={"X-Tenant": "beta"}),
                                       _session(row)))

    assert row.value["headers"] == {"X-Tenant": "beta"}


def test_invalid_results_are_a_422_listing_every_problem_and_nothing_is_saved():
    session = _session(_saved_row())

    with pytest.raises(RequestValidationError) as refused:
        asyncio.run(update_target_settings(
            TargetSettingsUpdate(url="ftp://nope", body={"prompt": "hi"}, max_retries=11),
            session,
        ))

    assert [(e["loc"], e["msg"].split(",")[0]) for e in refused.value.errors()] == [
        (("body", "url"), "Must be an http:// or https:// URL"),
        (("body", "body"), "Must contain {{input}} in at least one string value"),
        (("body", "max_retries"), "Input should be less than or equal to 10"),
    ]
    assert all("input" not in error for error in refused.value.errors())
    session.commit.assert_not_awaited()


def test_a_save_logs_the_field_names_never_the_values(caplog):
    with caplog.at_level(logging.INFO, logger="assay.services.settings"):
        asyncio.run(update_target_settings(
            TargetSettingsUpdate(url="https://token-in-url.example.test/?key=abc123"),
            _session(),
        ))

    (record,) = [r for r in caplog.records if r.name.startswith("assay.services.settings")]
    assert record.getMessage() == (
        "Saved the application-under-test settings (first save): url"
    )
    assert record.changed_fields == ["url"]
    assert "abc123" not in record.getMessage()


# --- DELETE ---


def test_reset_deletes_the_saved_row_and_returns_the_environments_settings():
    row = _saved_row()
    session = _session(row)

    result = asyncio.run(reset_target_settings(session))

    session.delete.assert_awaited_once_with(row)
    session.commit.assert_awaited_once()
    assert result.source == SettingsSource.environment
    assert result.url == "http://env.example.test/chat"


def test_resetting_with_nothing_saved_is_not_an_error():
    session = _session()

    result = asyncio.run(reset_target_settings(session))

    session.delete.assert_not_awaited()
    session.commit.assert_not_awaited()
    assert result.source == SettingsSource.environment
