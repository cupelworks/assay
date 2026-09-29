import pytest
from pydantic import ValidationError

from assay.schemas import TargetSettings
from assay.schemas.settings import describe_validation_error, settings_errors


def _problems(**fields) -> dict[str, str]:
    with pytest.raises(ValidationError) as refused:
        TargetSettings(**fields)
    return {".".join(map(str, e["loc"])): e["msg"] for e in settings_errors(refused.value)}


def test_the_defaults_are_assays_own_minimal_contract_with_no_application():
    settings = TargetSettings()

    assert settings.url is None
    assert (settings.method, settings.headers, settings.body) == ("POST", {},
                                                                   {"input": "{{input}}"})
    assert (settings.output_path, settings.timeout_seconds, settings.max_retries) == (
        "$.output", 60, 2)


# --- url ---


@pytest.mark.parametrize("url", ["http://localhost:8765/chat", "https://bot.example.com/v1"])
def test_an_http_or_https_url_is_accepted(url):
    assert TargetSettings(url=url).url == url


@pytest.mark.parametrize("url", ["ftp://bot.example.com", "bot.example.com/chat", "https://"])
def test_anything_else_is_refused(url):
    assert _problems(url=url) == {"url": "Must be an http:// or https:// URL"}


@pytest.mark.parametrize("url", [None, "", "   "])
def test_a_missing_or_blank_url_means_no_application(url):
    assert TargetSettings(url=url).url is None


# --- method ---


def test_the_method_is_stored_uppercased():
    assert TargetSettings(method="patch").method == "PATCH"


@pytest.mark.parametrize("method", ["GET", "DELETE", "FETCH"])
def test_a_method_that_carries_no_json_body_is_refused(method):
    assert _problems(method=method) == {"method": "Must be one of POST, PUT, PATCH"}


# --- headers ---


def test_headers_with_env_references_are_kept_exactly_as_written():
    headers = {"Authorization": "Bearer ${ASSAY_TARGET_API_KEY}", "X-Tenant": "acme"}

    assert TargetSettings(headers=headers).headers == headers


def test_every_header_problem_is_reported_together():
    problems = _problems(headers={
        "Bad Name": "x",
        "X-Key": "${1ABC}",
        "X-Split": "a\r\nInjected: yes",
    })

    assert problems["headers"] == (
        "'Bad Name' is not a valid header name; "
        "the value of 'X-Key' references ${1ABC}, which is not a valid variable name "
        "(letters, digits and _, not starting with a digit); "
        "the value of 'X-Split' contains a line break"
    )


# --- body ---


def test_the_input_placeholder_may_sit_anywhere_in_a_nested_body():
    body = {"messages": [{"role": "user", "content": "Q: {{input}}"}], "n": 1}

    assert TargetSettings(body=body).body == body


@pytest.mark.parametrize("body", [{}, {"prompt": "hello"}, {"n": 1, "list": [2, 3]}])
def test_a_body_without_the_placeholder_is_refused(body):
    assert _problems(body=body)["body"].startswith("Must contain {{input}}")


# --- output_path, timeout, retries ---


@pytest.mark.parametrize("path", ["$", "$.output", "$.result.category", "$.items[0].sku"])
def test_a_valid_jsonpath_is_accepted_including_the_root(path):
    assert TargetSettings(output_path=path).output_path == path


@pytest.mark.parametrize("path", ["$[", "foo bar", "$.a[?"])
def test_an_invalid_jsonpath_is_refused(path):
    assert _problems(output_path=path)["output_path"].startswith("Not a valid JSONPath")


@pytest.mark.parametrize("timeout", [0, -1, 601])
def test_the_timeout_is_above_0_and_at_most_600(timeout):
    assert "timeout_seconds" in _problems(timeout_seconds=timeout)


@pytest.mark.parametrize("retries", [-1, 11])
def test_retries_are_0_to_10(retries):
    assert "max_retries" in _problems(max_retries=retries)


def test_an_unknown_field_is_refused():
    assert "retries" in _problems(retries=3)


# --- reporting ---


def test_every_problem_is_reported_at_once_without_the_values():
    with pytest.raises(ValidationError) as refused:
        TargetSettings(url="ftp://secret-host", method="GET", max_retries=99)

    line = describe_validation_error(refused.value)

    assert line == (
        "url: Must be an http:// or https:// URL; method: Must be one of POST, PUT, PATCH; "
        "max_retries: Input should be less than or equal to 10"
    )
    assert "secret-host" not in line


def test_with_a_prefix_fields_are_named_as_environment_variables():
    with pytest.raises(ValidationError) as refused:
        TargetSettings(max_retries=-1)

    assert describe_validation_error(refused.value, prefix="ASSAY_TARGET_") == (
        "ASSAY_TARGET_MAX_RETRIES: Input should be greater than or equal to 0"
    )
