import json
import logging
from unittest.mock import patch

import httpx
import pytest

from assay.schemas import TargetSettings
from assay.worker import target
from assay.worker.target import TargetError, TargetResponse, get_answer

URL = "https://app.example.test/api/chat"


def _settings(**overrides) -> TargetSettings:
    """A fake application with the default contract, plus overrides."""
    return TargetSettings(**{"url": URL, "timeout_seconds": 5, **overrides})


SETTINGS = _settings()


@pytest.fixture
def configured():
    """No sleeping between retries; yields the patched sleep."""
    with patch("assay.worker.target.time.sleep") as sleep:
        yield sleep


def _transport(handler):
    return httpx.MockTransport(handler)


def _ok(payload):
    return _transport(lambda request: httpx.Response(200, json=payload))


# --- request shape ---


def test_sends_the_input_inside_the_json_template_and_reads_the_answer(configured):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"output": "Go to Settings"})

    response = get_answer("How do I reset?", SETTINGS, transport=_transport(handler))

    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == URL
    assert json.loads(request.content) == {"input": "How do I reset?"}
    assert response == TargetResponse(answer="Go to Settings", status=200,
                                      latency_ms=response.latency_ms, attempts=1)


def test_quotes_and_newlines_in_the_input_cannot_break_or_inject_into_the_body(configured):
    settings = _settings(body={"messages": [{"role": "user", "content": "Q: {{input}}"}], "n": 1})
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"output": "fine"})

    hostile = 'He said "hi",\n"admin": true'
    get_answer(hostile, settings, transport=_transport(handler))

    assert seen[0] == {"messages": [{"role": "user", "content": f"Q: {hostile}"}], "n": 1}


def test_headers_resolve_env_var_references_and_secrets_never_appear_in_settings(
        configured, monkeypatch):
    settings = _settings(headers={"Authorization": "Bearer ${ASSAY_TARGET_API_KEY}",
                                  "X-Static": "v1"})
    monkeypatch.setenv("ASSAY_TARGET_API_KEY", "s3cret")
    seen = []

    def handler(request):
        seen.append(dict(request.headers))
        return httpx.Response(200, json={"output": "ok"})

    get_answer("q", settings, transport=_transport(handler))

    assert seen[0]["authorization"] == "Bearer s3cret"
    assert seen[0]["x-static"] == "v1"


def test_an_unset_env_var_in_a_header_is_a_clear_failure_before_any_call(configured,
                                                                         monkeypatch):
    settings = _settings(headers={"Authorization": "Bearer ${NOPE}"})
    monkeypatch.delenv("NOPE", raising=False)

    with pytest.raises(TargetError,
                       match=r"a header references \$\{NOPE\} but NOPE is not set on this server"):
        get_answer("q", settings, transport=_transport(lambda r: pytest.fail("must not be called")))


def test_method_comes_from_settings(configured):
    settings = _settings(method="PUT")
    seen = []

    def handler(request):
        seen.append(request.method)
        return httpx.Response(200, json={"output": "ok"})

    get_answer("q", settings, transport=_transport(handler))

    assert seen == ["PUT"]


# --- reading the answer ---


def test_a_nested_output_path_finds_the_answer(configured):
    settings = _settings(output_path="$.choices[0].message.content")

    response = get_answer("q", settings, transport=_ok(
        {"choices": [{"message": {"role": "assistant", "content": "nested"}}]}
    ))

    assert response.answer == "nested"


@pytest.mark.parametrize("payload,expected", [
    ({"answer": "x"}, "nothing found at output path '\\$.output'"),
    ({"output": None}, "is NoneType, not a text answer"),
    ({"output": ""}, "is empty, not a text answer"),
    ({"output": "   "}, "is empty, not a text answer"),
    ({"output": 42}, "is int, not a text answer"),
    ({"output": {"text": "x"}}, "is dict, not a text answer"),
])
def test_no_usable_answer_at_the_path_is_a_failure_naming_the_path(configured, payload,
                                                                    expected):
    with pytest.raises(TargetError, match=expected):
        get_answer("q", SETTINGS, transport=_ok(payload))


def test_a_non_json_reply_is_a_failure(configured):
    transport = _transport(lambda r: httpx.Response(200, text="<html>oops</html>"))

    with pytest.raises(TargetError, match="reply is not JSON"):
        get_answer("q", SETTINGS, transport=transport)


def test_an_invalid_output_path_is_still_a_failure_before_any_call(configured):
    # TargetSettings refuses one; the adapter stays defensive regardless
    settings = SETTINGS.model_copy(update={"output_path": "$["})

    with pytest.raises(TargetError, match="output path '\\$\\[' is not a valid JSONPath"):
        get_answer("q", settings, transport=_transport(lambda r: pytest.fail("must not be called")))


# --- not configured ---


def test_no_url_is_a_failure_that_says_so(configured):
    with pytest.raises(TargetError, match="no application configured: no URL is set"):
        get_answer("q", _settings(url=None))


# --- retries ---


def _flaky(failures, then=None):
    """A handler that fails `failures` times (each a Response or an exception),
    then answers."""
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) <= len(failures):
            outcome = failures[len(calls) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return httpx.Response(200, json={"output": then or "recovered"})

    return handler, calls


@pytest.mark.parametrize("failure", [
    httpx.Response(500), httpx.Response(502), httpx.Response(503), httpx.Response(429),
    httpx.ConnectError("refused"), httpx.ReadTimeout("slow"),
])
def test_retryable_failures_are_retried_with_backoff_and_the_attempt_count_reported(
        configured, failure):
    handler, calls = _flaky([failure])

    response = get_answer("q", SETTINGS, transport=_transport(handler))

    assert response.answer == "recovered"
    assert (response.attempts, len(calls)) == (2, 2)
    configured.assert_called_once_with(1.0)


def test_backoff_doubles_one_second_then_two(configured):
    handler, calls = _flaky([httpx.Response(503), httpx.Response(503)])

    response = get_answer("q", SETTINGS, transport=_transport(handler))

    assert response.attempts == 3
    assert [c.args[0] for c in configured.call_args_list] == [1.0, 2.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_other_4xx_are_never_retried(configured, status):
    handler, calls = _flaky([httpx.Response(status)])

    with pytest.raises(TargetError, match=f"application answered HTTP {status}$"):
        get_answer("q", SETTINGS, transport=_transport(handler))

    assert len(calls) == 1
    configured.assert_not_called()


def test_exhausting_the_retries_fails_with_the_last_reason_and_the_attempt_count(configured):
    handler, calls = _flaky([httpx.Response(503)] * 3)

    with pytest.raises(TargetError, match="application answered HTTP 503 after 3 attempt"):
        get_answer("q", SETTINGS, transport=_transport(handler))

    assert len(calls) == 3


def test_max_retries_zero_means_exactly_one_call(configured):
    handler, calls = _flaky([httpx.Response(503)])

    with pytest.raises(TargetError, match="after 1 attempt"):
        get_answer("q", _settings(max_retries=0), transport=_transport(handler))

    assert len(calls) == 1
    configured.assert_not_called()


def test_retry_after_in_seconds_wins_over_the_backoff(configured):
    handler, _ = _flaky([httpx.Response(429, headers={"Retry-After": "7"})])

    get_answer("q", SETTINGS, transport=_transport(handler))

    configured.assert_called_once_with(7.0)


def test_retry_after_is_capped(configured):
    handler, _ = _flaky([httpx.Response(429, headers={"Retry-After": "3600"})])

    get_answer("q", SETTINGS, transport=_transport(handler))

    configured.assert_called_once_with(target.MAX_RETRY_AFTER_SECONDS)


def test_an_unparseable_retry_after_falls_back_to_the_backoff(configured):
    handler, _ = _flaky([httpx.Response(429, headers={"Retry-After": "soon"})])

    get_answer("q", SETTINGS, transport=_transport(handler))

    configured.assert_called_once_with(1.0)


# --- logging ---


def _target_records(caplog):
    # httpx logs its own INFO line per request; only the adapter's lines count here
    return [r for r in caplog.records if r.name == "assay.worker.target"]


def test_logs_one_info_line_per_answer_and_never_the_texts(configured, caplog):
    with caplog.at_level(logging.INFO, logger="assay.worker.target"):
        get_answer("the secret prompt", SETTINGS, transport=_ok({"output": "the secret answer"}))

    (record,) = _target_records(caplog)
    assert record.getMessage().startswith("Application answered HTTP 200 in ")
    assert (record.status, record.attempt, record.attempts) == (200, 1, 3)
    assert "secret" not in record.getMessage()


def test_logs_a_warning_per_retried_attempt_and_an_error_when_giving_up(configured, caplog):
    handler, _ = _flaky([httpx.Response(503)] * 3)

    with caplog.at_level(logging.INFO, logger="assay.worker.target"), pytest.raises(TargetError):
        get_answer("q", SETTINGS, transport=_transport(handler))

    records = _target_records(caplog)
    assert [r.levelno for r in records] == [logging.WARNING, logging.WARNING, logging.ERROR]
    assert records[0].getMessage() == (
        "application answered HTTP 503 (attempt 1 of 3); retrying in 1.0 s"
    )
    assert records[-1].getMessage() == (
        "application answered HTTP 503 after 3 attempt(s); giving up"
    )


# --- what a failure carries ---


def test_a_failure_after_a_response_carries_its_status_and_latency(configured):
    with pytest.raises(TargetError) as refused:
        get_answer("q", SETTINGS, transport=_transport(lambda r: httpx.Response(401)))

    assert refused.value.status == 401
    assert refused.value.latency_ms is not None


def test_a_2xx_without_a_usable_answer_still_carries_the_status(configured):
    with pytest.raises(TargetError) as no_answer:
        get_answer("q", SETTINGS, transport=_ok({"answer": "wrong place"}))

    assert no_answer.value.status == 200


def test_exhausted_retries_carry_the_last_responses_status(configured):
    handler, _ = _flaky([httpx.Response(503)] * 3)

    with pytest.raises(TargetError) as exhausted:
        get_answer("q", SETTINGS, transport=_transport(handler))

    assert exhausted.value.status == 503


def test_a_failure_with_no_response_carries_no_status(configured):
    def refuse(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(TargetError) as unreachable:
        get_answer("q", _settings(max_retries=0), transport=_transport(refuse))

    assert (unreachable.value.status, unreachable.value.latency_ms) == (None, None)
