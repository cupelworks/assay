import pytest
from pydantic import ValidationError

from assay.config import Settings


def test_log_level_is_normalized_to_upper_case():
    assert Settings(_env_file=None, log_level="debug").log_level == "DEBUG"


def test_unknown_log_level_is_rejected_at_startup():
    with pytest.raises(ValidationError, match="unknown log level 'LOUD'"):
        Settings(_env_file=None, log_level="LOUD")


def test_log_format_only_accepts_text_or_json():
    assert Settings(_env_file=None, log_format="json").log_format == "json"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_format="xml")


# --- the application under test (ASSAY_TARGET_*) ---


def test_target_defaults_are_assays_own_minimal_contract():
    settings = Settings(_env_file=None)

    assert settings.target_url is None
    assert settings.target_method == "POST"
    assert settings.target_headers == {}
    assert settings.target_body == {"input": "{{input}}"}
    assert settings.target_output_path == "$.output"
    assert (settings.target_timeout_seconds, settings.target_max_retries) == (60, 2)


def test_target_method_is_normalised_to_upper_case():
    assert Settings(_env_file=None, target_method="post").target_method == "POST"


def test_target_headers_and_body_are_parsed_from_json_strings(monkeypatch):
    monkeypatch.setenv("ASSAY_TARGET_HEADERS", '{"Authorization": "Bearer ${KEY}"}')
    monkeypatch.setenv(
        "ASSAY_TARGET_BODY", '{"messages": [{"role": "user", "content": "{{input}}"}]}',
    )

    settings = Settings(_env_file=None)

    assert settings.target_headers == {"Authorization": "Bearer ${KEY}"}
    assert settings.target_body == {"messages": [{"role": "user", "content": "{{input}}"}]}


def test_invalid_json_in_target_body_fails_at_startup(monkeypatch):
    monkeypatch.setenv("ASSAY_TARGET_BODY", "{not json")

    with pytest.raises(Exception, match="target_body"):
        Settings(_env_file=None)


def test_negative_target_max_retries_is_rejected():
    with pytest.raises(ValueError, match="ASSAY_TARGET_MAX_RETRIES: Input should be greater "
                                         "than or equal to 0"):
        Settings(_env_file=None, target_max_retries=-1)


def test_the_environment_gets_the_same_rules_as_a_save_from_the_ui_all_reported_at_once():
    with pytest.raises(ValidationError) as refused:
        Settings(_env_file=None, target_url="ftp://app", target_method="DELETE",
                 target_body={"prompt": "no placeholder"}, target_output_path="$[",
                 target_timeout_seconds=0)

    message = str(refused.value)
    for field in ("URL", "METHOD", "BODY", "OUTPUT_PATH", "TIMEOUT_SECONDS"):
        assert f"ASSAY_TARGET_{field}:" in message


def test_a_blank_target_url_means_no_application():
    assert Settings(_env_file=None, target_url="").target_settings().url is None


def test_target_settings_are_the_environments_values():
    settings = Settings(_env_file=None, target_url="http://app.test/chat", target_method="put",
                        target_max_retries=0)

    target = settings.target_settings()

    assert (target.url, target.method, target.max_retries) == ("http://app.test/chat", "PUT", 0)
