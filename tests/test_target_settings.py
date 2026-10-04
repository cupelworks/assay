from datetime import datetime

import pytest
from pydantic import ValidationError

from assay.config import settings
from assay.models import SettingsModel, SettingsSection
from assay.schemas import SettingsSource
from assay.target_settings import resolve_target_settings
from assay.timestamps import as_utc


def test_with_no_saved_row_the_environment_applies(monkeypatch):
    monkeypatch.setattr(settings, "target_url", "http://env.example.test/chat")

    resolved = resolve_target_settings(None)

    assert resolved.source == SettingsSource.environment
    assert resolved.updated_at is None
    assert resolved.url == "http://env.example.test/chat"


def test_a_saved_row_is_the_whole_truth_whatever_the_environment_says(monkeypatch):
    monkeypatch.setattr(settings, "target_url", "http://env.example.test/chat")
    monkeypatch.setattr(settings, "target_max_retries", 5)
    saved_at = datetime(2026, 9, 27, 15, 10)
    row = SettingsModel(section=SettingsSection.target,
                        value={"url": "https://saved.example.test/chat"}, updated_at=saved_at)

    resolved = resolve_target_settings(row)

    assert (resolved.source, resolved.updated_at) == (SettingsSource.database, as_utc(saved_at))
    assert resolved.url == "https://saved.example.test/chat"
    # not mixed field by field: a field the row lacks is the default, not the env's 5
    assert resolved.max_retries == 2


def test_a_saved_row_that_no_longer_validates_raises_rather_than_falling_back():
    row = SettingsModel(section=SettingsSection.target, value={"url": "ftp://nope"},
                        updated_at=datetime(2026, 9, 27))

    with pytest.raises(ValidationError):
        resolve_target_settings(row)
