from datetime import datetime

import pytest
from pydantic import ValidationError

from assay.config import Settings, settings
from assay.judge_settings import resolve_judge_settings
from assay.models import SettingsModel, SettingsSection
from assay.schemas import JudgeProvider, JudgeSettings, SettingsSource

# --- the rules (JudgeSettings) ---


def test_the_defaults_are_no_judge():
    judge = JudgeSettings()

    assert (judge.provider, judge.model, judge.base_url, judge.api_key_env) == (
        None, None, None, None)
    assert (judge.timeout_seconds, judge.max_retries) == (60, 2)
    assert (judge.key_variable(), judge.api_root()) == (None, None)


def test_a_provider_needs_a_model_and_the_problem_is_on_the_model_field():
    with pytest.raises(ValidationError) as caught:
        JudgeSettings(provider="anthropic")

    (error,) = caught.value.errors()
    assert error["loc"] == ("model",)
    assert error["msg"] == "Value error, Required when a provider is set"


@pytest.mark.parametrize("model", ["", "   "])
def test_a_blank_model_is_no_model(model):
    assert JudgeSettings(model=model).model is None
    with pytest.raises(ValidationError, match="Required when a provider is set"):
        JudgeSettings(provider="openai", model=model)


def test_every_problem_is_reported_at_once():
    with pytest.raises(ValidationError) as caught:
        JudgeSettings(provider="mistral", model="m", base_url="ftp://x", api_key_env="1KEY",
                      timeout_seconds=0, max_retries=11)

    assert {error["loc"][0] for error in caught.value.errors()} == {
        "provider", "base_url", "api_key_env", "timeout_seconds", "max_retries"}


def test_the_key_variable_and_api_root_default_to_the_providers_own():
    anthropic = JudgeSettings(provider="anthropic", model="claude-sonnet-5-5")
    openai = JudgeSettings(provider="openai", model="gpt-4o-mini")

    assert (anthropic.key_variable(), anthropic.api_root()) == (
        "ANTHROPIC_API_KEY", "https://api.anthropic.com")
    assert (openai.key_variable(), openai.api_root()) == (
        "OPENAI_API_KEY", "https://api.openai.com/v1")


def test_a_custom_key_variable_and_base_url_win():
    judge = JudgeSettings(provider="openai", model="llama3", api_key_env=" LOCAL_KEY ",
                          base_url="http://localhost:11434/v1/")

    assert judge.key_variable() == "LOCAL_KEY"
    assert judge.api_root() == "http://localhost:11434/v1"


def test_the_model_name_is_bounded():
    with pytest.raises(ValidationError):
        JudgeSettings(provider="openai", model="m" * 201)


# --- the environment (ASSAY_JUDGE_*) ---


def test_the_environment_defaults_to_no_judge():
    assert Settings(_env_file=None).judge_settings() == JudgeSettings()


def test_the_environment_gets_the_same_rules_as_a_save_all_reported_at_once():
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, judge_provider="anthropic", judge_api_key_env="1KEY")

    message = str(caught.value)
    assert "ASSAY_JUDGE_MODEL: Required when a provider is set" in message
    assert "ASSAY_JUDGE_API_KEY_ENV: Must be a valid variable name" in message


def test_the_environments_values_become_the_judge_settings():
    judge = Settings(_env_file=None, judge_provider="openai", judge_model="gpt-4o-mini",
                     judge_timeout_seconds=30).judge_settings()

    assert (judge.provider, judge.model, judge.timeout_seconds) == (
        JudgeProvider.openai, "gpt-4o-mini", 30)


def test_a_blank_provider_in_the_environment_is_no_judge():
    assert Settings(_env_file=None, judge_provider="").judge_settings().provider is None


# --- which settings are in effect ---


def test_with_no_saved_row_the_environment_applies(monkeypatch):
    monkeypatch.setattr(settings, "judge_provider", "anthropic")
    monkeypatch.setattr(settings, "judge_model", "claude-haiku-4-5-20251001")

    effective = resolve_judge_settings(None)

    assert (effective.source, effective.updated_at) == (SettingsSource.environment, None)
    assert effective.model == "claude-haiku-4-5-20251001"
    assert effective.api_key_variable == "ANTHROPIC_API_KEY"


def test_a_saved_row_is_the_whole_truth(monkeypatch):
    monkeypatch.setattr(settings, "judge_provider", "anthropic")
    monkeypatch.setattr(settings, "judge_model", "claude-haiku-4-5-20251001")
    saved_at = datetime(2026, 10, 1, 9, 30)
    row = SettingsModel(section=SettingsSection.judge, updated_at=saved_at,
                        value={"provider": "openai", "model": "gpt-4o-mini",
                               "api_key_env": "TEAM_KEY"})

    effective = resolve_judge_settings(row)

    assert (effective.source, effective.updated_at) == (SettingsSource.database, saved_at)
    assert (effective.provider, effective.model) == (JudgeProvider.openai, "gpt-4o-mini")
    assert effective.api_key_variable == "TEAM_KEY"


def test_a_saved_row_that_no_longer_validates_raises():
    row = SettingsModel(section=SettingsSection.judge, updated_at=datetime(2026, 10, 1),
                        value={"provider": "anthropic"})

    with pytest.raises(ValidationError):
        resolve_judge_settings(row)
