import json
import logging

import httpx
import pytest

from assay.schemas import JudgeSettings
from assay.worker import llm

ANTHROPIC = JudgeSettings(provider="anthropic", model="claude-sonnet-5-5", max_retries=2)
OPENAI = JudgeSettings(provider="openai", model="gpt-4o-mini", max_retries=2)


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setattr(llm.time, "sleep", lambda seconds: None)


def _anthropic_reply(verdict=None, stop_reason="tool_use", **extra):
    content = [{"type": "tool_use", "name": "record_verdict", "id": "t1",
                "input": verdict if verdict is not None else
                {"passed": True, "rationale": "It answers the question."}}]
    return {"content": content, "stop_reason": stop_reason,
            "usage": {"input_tokens": 312, "output_tokens": 41}, **extra}


def _openai_reply(content=None, refusal=None):
    content = content if content is not None else json.dumps(
        {"passed": False, "rationale": "It talks about something else."})
    return {"choices": [{"message": {"role": "assistant", "content": content,
                                     "refusal": refusal}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 280, "completion_tokens": 35}}


def _transport(*replies):
    """Answers each request with the next reply: a (status, json) pair, or an
    exception to raise. Records the requests."""
    requests = []
    queue = list(replies)

    def handle(request):
        requests.append(request)
        reply = queue.pop(0)
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        return httpx.Response(status, json=body, headers={"Retry-After": "0"})

    transport = httpx.MockTransport(handle)
    transport.requests = requests
    return transport


def _ask(settings, transport):
    return llm.ask_for_verdict(settings, "system prompt", "the prompt", transport=transport)


# --- Anthropic ---


def test_anthropic_forces_the_verdict_tool_at_temperature_0():
    transport = _transport((200, _anthropic_reply()))

    verdict = _ask(ANTHROPIC, transport)

    (request,) = transport.requests
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "sk-ant-test"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert (body["model"], body["temperature"], body["max_tokens"]) == (
        "claude-sonnet-5-5", 0, 1024)
    assert body["system"] == "system prompt"
    assert body["messages"] == [{"role": "user", "content": "the prompt"}]
    assert body["tool_choice"] == {"type": "tool", "name": "record_verdict"}
    assert body["tools"][0]["input_schema"]["required"] == ["passed", "rationale"]
    assert verdict == llm.Verdict(passed=True, rationale="It answers the question.",
                                  status=200, latency_ms=verdict.latency_ms,
                                  input_tokens=312, output_tokens=41)


def test_anthropic_a_refusal_is_the_reason():
    transport = _transport((200, _anthropic_reply(stop_reason="refusal")))

    with pytest.raises(llm.JudgeError, match="The judge refused to give a verdict") as caught:
        _ask(ANTHROPIC, transport)
    assert caught.value.status == 200


def test_anthropic_a_reply_without_the_tool_call_is_unreadable():
    reply = {"content": [{"type": "text", "text": "I think it passes."}], "stop_reason": "end"}

    with pytest.raises(llm.JudgeError, match="couldn't be read: no verdict in it"):
        _ask(ANTHROPIC, _transport((200, reply)))


@pytest.mark.parametrize("verdict", [
    {"passed": "yes", "rationale": "Fine."},
    {"passed": True},
    {"passed": True, "rationale": "  "},
])
def test_a_verdict_needs_a_boolean_and_a_rationale(verdict):
    with pytest.raises(llm.JudgeError, match="needs a true/false `passed` and a `rationale`"):
        _ask(ANTHROPIC, _transport((200, _anthropic_reply(verdict))))


def test_a_reply_that_is_not_json_is_unreadable():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>"))

    with pytest.raises(llm.JudgeError, match="couldn't be read: it isn't a JSON object"):
        _ask(ANTHROPIC, transport)


# --- OpenAI ---


def test_openai_asks_for_strict_json_at_temperature_0():
    transport = _transport((200, _openai_reply()))

    verdict = _ask(OPENAI, transport)

    (request,) = transport.requests
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-openai-test"
    body = json.loads(request.content)
    assert (body["model"], body["temperature"], body["max_completion_tokens"]) == (
        "gpt-4o-mini", 0, 1024)
    assert body["messages"] == [{"role": "system", "content": "system prompt"},
                                {"role": "user", "content": "the prompt"}]
    assert body["response_format"]["json_schema"]["strict"] is True
    assert (verdict.passed, verdict.rationale) == (False, "It talks about something else.")
    assert (verdict.input_tokens, verdict.output_tokens) == (280, 35)


def test_openai_a_refusal_is_the_reason():
    with pytest.raises(llm.JudgeError, match="refused"):
        _ask(OPENAI, _transport((200, _openai_reply(content=None, refusal="I can't help."))))


def test_openai_a_verdict_that_is_not_json_is_unreadable():
    with pytest.raises(llm.JudgeError, match="its verdict isn't JSON"):
        _ask(OPENAI, _transport((200, _openai_reply(content="Pass."))))


def test_openai_a_reply_without_a_message_is_unreadable():
    with pytest.raises(llm.JudgeError, match="no message in it"):
        _ask(OPENAI, _transport((200, {"choices": []})))


def test_a_url_is_called_exactly_as_written_with_its_own_key(monkeypatch):
    monkeypatch.setenv("LOCAL_KEY", "local")
    settings = OPENAI.model_copy(update={"url": "http://localhost:11434/v1/chat/completions",
                                         "api_key_env": "LOCAL_KEY"})
    transport = _transport((200, _openai_reply()))

    _ask(settings, transport)

    (request,) = transport.requests
    assert str(request.url) == "http://localhost:11434/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer local"


# --- configuration ---


def test_no_provider_is_no_judge_and_nothing_is_called():
    transport = _transport()

    with pytest.raises(llm.JudgeError, match="No judge configured: choose a provider and model"):
        _ask(JudgeSettings(), transport)
    assert transport.requests == []


def test_an_unset_key_variable_is_named_and_nothing_is_called(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    transport = _transport()

    with pytest.raises(llm.JudgeError, match="^ANTHROPIC_API_KEY is not set on this server$"):
        _ask(ANTHROPIC, transport)
    assert transport.requests == []


# --- failures and retries ---


def test_a_4xx_is_not_retried_and_carries_the_providers_message():
    error = {"type": "error", "error": {"type": "authentication_error",
                                        "message": "invalid x-api-key"}}
    transport = _transport((401, error))

    with pytest.raises(llm.JudgeError) as caught:
        _ask(ANTHROPIC, transport)

    assert str(caught.value) == ("Judge answered HTTP 401 at https://api.anthropic.com/v1/"
                                 "messages: invalid x-api-key")
    assert caught.value.status == 401
    assert len(transport.requests) == 1


def test_a_long_provider_message_is_cut():
    transport = _transport((400, {"error": {"message": "x" * 500}}))

    with pytest.raises(llm.JudgeError) as caught:
        _ask(OPENAI, transport)

    provider_message = str(caught.value).split(": ", 1)[1]
    assert len(provider_message) == 200
    assert provider_message.endswith("…")


def test_a_429_is_retried_then_answered():
    transport = _transport((429, {}), (200, _anthropic_reply()))

    assert _ask(ANTHROPIC, transport).passed is True
    assert len(transport.requests) == 2


def test_5xx_until_the_retries_run_out():
    transport = _transport((529, {}), (529, {}), (529, {}))

    with pytest.raises(llm.JudgeError, match="Judge answered HTTP 529 at .* after 3 attempt"):
        _ask(ANTHROPIC, transport)


def test_a_connection_error_is_retried_and_named():
    transport = _transport(httpx.ConnectError("refused"), httpx.ConnectError("refused"),
                           httpx.ConnectError("refused"))

    with pytest.raises(llm.JudgeError) as caught:
        _ask(ANTHROPIC, transport)

    assert str(caught.value) == ("ConnectError calling the judge at https://api.anthropic.com/"
                                 "v1/messages after 3 attempt(s)")
    assert caught.value.status is None


def test_no_retries_means_one_call():
    transport = _transport((503, {}))

    with pytest.raises(llm.JudgeError, match="after 1 attempt"):
        _ask(ANTHROPIC.model_copy(update={"max_retries": 0}), transport)
    assert len(transport.requests) == 1


# --- logging ---


def test_a_call_logs_model_latency_and_tokens_never_the_prompt_the_reply_or_the_key(caplog):
    with caplog.at_level(logging.INFO, logger="assay.worker.llm"):
        _ask(ANTHROPIC, _transport((200, _anthropic_reply())))

    assert "Judge claude-sonnet-5-5 answered HTTP 200" in caplog.text
    assert "312 in / 41 out tokens" in caplog.text
    for secret in ("the prompt", "It answers the question.", "sk-ant-test"):
        assert secret not in caplog.text


def test_a_wrong_url_shows_in_the_reason_without_its_query_string():
    settings = ANTHROPIC.model_copy(update={"url": "http://localhost:8001/chat?token=s3cret"})

    with pytest.raises(llm.JudgeError) as caught:
        _ask(settings, _transport((404, {"detail": "Not Found"})))

    assert str(caught.value) == "Judge answered HTTP 404 at http://localhost:8001/chat"


# --- settings as a run resolves them ---


@pytest.mark.parametrize("settings,url", [
    (ANTHROPIC, "https://api.anthropic.com/v1/messages"),
    (JudgeSettings(provider="openai", model="m", url="http://127.0.0.1:8766/v1/chat/completions"),
     "http://127.0.0.1:8766/v1/chat/completions"),
])
def test_the_settings_a_run_resolves_are_called_at_their_url(settings, url):
    # execute_run hands over resolve_judge_settings()'s JudgeSettingsRead, whose
    # `endpoint` field once shadowed the endpoint() method this client called,
    # so every judge check in a run failed with "'str' object is not callable"
    from assay.judge_settings import judge_settings_read
    from assay.schemas import SettingsSource

    resolved = judge_settings_read(settings, SettingsSource.database, None)
    reply = _anthropic_reply() if settings.provider == "anthropic" else _openai_reply()
    transport = _transport((200, reply))

    assert _ask(resolved, transport).status == 200
    (request,) = transport.requests
    assert str(request.url) == url
