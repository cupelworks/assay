# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
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
from assay.models import SettingsModel, SettingsSection, TargetCheckModel, TargetCheckStatus
from assay.schemas import TargetCheckRequest, TargetSettingsUpdate
from assay.services import create_target_check, get_target_check

_PATCH_SEND = "assay.services.settings.create_target_check._celery_app.send_task"


def _session(row=None) -> AsyncMock:
    session = AsyncMock()
    session.get.return_value = row
    session.add = lambda obj: session.added.append(obj)
    session.added = []
    return session


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setattr(settings, "target_url", "http://env.example.test/chat")


def test_a_check_stores_the_settings_in_effect_and_is_sent_to_a_worker():
    session = _session()

    with patch(_PATCH_SEND) as send:
        check = asyncio.run(create_target_check(TargetCheckRequest(input="Hello?"), session))

    (row,) = session.added
    assert (row.input, row.status) == ("Hello?", TargetCheckStatus.pending)
    assert row.settings["url"] == "http://env.example.test/chat"
    session.commit.assert_awaited_once()
    send.assert_called_once()
    assert send.call_args.args == ("assay.worker.tasks.check_target.check_target",)
    assert send.call_args.kwargs["args"] == [row.id]
    assert send.call_args.kwargs["ignore_result"] is True
    assert (check.id, check.status, check.ok) == (row.id, TargetCheckStatus.pending, None)


def test_proposed_settings_are_checked_but_never_saved():
    saved = SettingsModel(section=SettingsSection.target,
                          value={"url": "https://saved.example.test/chat"},
                          updated_at=datetime(2026, 9, 27))
    session = _session(saved)
    request = TargetCheckRequest(
        input="Hello?", settings=TargetSettingsUpdate(output_path="$.reply.text"),
    )

    with patch(_PATCH_SEND):
        check = asyncio.run(create_target_check(request, session))

    assert check.settings.url == "https://saved.example.test/chat"
    assert check.settings.output_path == "$.reply.text"
    assert saved.value == {"url": "https://saved.example.test/chat"}
    assert [type(obj) for obj in session.added] == [TargetCheckModel]


def test_invalid_proposed_settings_are_a_422_pointing_inside_settings_and_no_check():
    session = _session()
    request = TargetCheckRequest(input="Hello?", settings=TargetSettingsUpdate(method="GET"))

    with patch(_PATCH_SEND) as send, pytest.raises(RequestValidationError) as refused:
        asyncio.run(create_target_check(request, session))

    assert [e["loc"] for e in refused.value.errors()] == [("body", "settings", "method")]
    assert session.added == []
    send.assert_not_called()


def test_a_check_that_cannot_be_sent_completes_at_once_with_the_reason(caplog):
    session = _session()

    with patch(_PATCH_SEND, side_effect=OperationalError("broker down")), \
            caplog.at_level(logging.ERROR, logger="assay.services.settings"):
        check = asyncio.run(create_target_check(TargetCheckRequest(input="Hello?"), session))

    assert check.status == TargetCheckStatus.completed
    assert (check.ok, check.error) == (False, "Could not be sent to a worker")
    assert check.completed_at is not None
    assert session.commit.await_count == 2
    assert any(r.exc_info for r in caplog.records)


def test_creating_a_check_logs_proposed_field_names_never_the_input_or_values(caplog):
    request = TargetCheckRequest(
        input="my secret prompt",
        settings=TargetSettingsUpdate(url="https://secret-host.example.test/chat"),
    )

    with patch(_PATCH_SEND), caplog.at_level(logging.INFO, logger="assay.services.settings"):
        asyncio.run(create_target_check(request, _session()))

    (record,) = [r for r in caplog.records if r.name.startswith("assay.services.settings")]
    assert record.proposed_fields == ["url"]
    assert "secret" not in record.getMessage()


def test_reading_a_check_returns_it_as_far_as_it_has_got():
    check_id = uuid.uuid4()
    row = TargetCheckModel(
        id=check_id, created_at=datetime(2026, 9, 27, 15, 12), status=TargetCheckStatus.completed,
        input="Hello?", settings={"url": "http://app.test/chat"}, ok=True, status_code=200,
        latency_ms=412.5, answer="Hi!", completed_at=datetime(2026, 9, 27, 15, 12, 1),
    )
    session = _session(row)

    check = asyncio.run(get_target_check(check_id, session))

    session.get.assert_awaited_once_with(TargetCheckModel, check_id)
    assert (check.ok, check.status_code, check.answer) == (True, 200, "Hi!")
    assert check.settings.url == "http://app.test/chat"


def test_an_unknown_check_is_a_404():
    check_id = uuid.uuid4()

    with pytest.raises(HTTPException) as missing:
        asyncio.run(get_target_check(check_id, _session()))

    assert missing.value.status_code == 404
    assert missing.value.detail == f"Check with ID '{check_id}' not found"
