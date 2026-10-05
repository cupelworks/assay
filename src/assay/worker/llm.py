# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""The client that asks a judge model for a verdict.

Plain HTTP (`httpx`), one request shape per provider, each posted to the
settings' endpoint exactly as written (their `url`, else the provider's own):
- `anthropic` — the Messages API, the verdict recorded through a single tool
  the model is made to call (`tool_choice`), which every Claude model
  supports;
- `openai` — the Chat Completions API, the verdict as JSON matching a strict
  `json_schema` response format; the endpoint can be any OpenAI-compatible
  server's.

Every failure's reason names the endpoint (without its query string), so a
wrong URL shows at once.

Temperature 0, at most MAX_OUTPUT_TOKENS. The API key is read here, in the
calling process, from the variable the settings name — never stored,
never logged. Retries follow the application-under-test adapter's rule
(worker/target.py): connection errors, timeouts, 5xx and 429 (honouring
Retry-After), nothing else. A reply that isn't a readable verdict is not
retried: the call already happened, and at temperature 0 it would likely
come back the same.

Each call logs the provider, model, status, latency and tokens — never the
prompt or the reply.
"""
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from assay.config import environment_value
from assay.schemas.settings import JudgeProvider, JudgeSettings
from assay.worker.target import RETRYABLE_STATUSES, _backoff

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 1024
ANTHROPIC_VERSION = "2023-06-01"
TOOL_NAME = "record_verdict"
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {
            "type": "boolean",
            "description": "Whether the answer passes the rubric.",
        },
        "rationale": {
            "type": "string",
            "description": "Why, in two to four plain sentences, pointing at the part of "
                           "the answer the verdict rests on.",
        },
    },
    "required": ["passed", "rationale"],
    "additionalProperties": False,
}
# How much of a provider's own error message goes into the reason
_MAX_PROVIDER_MESSAGE = 200


class JudgeError(Exception):
    """The judge could not give a verdict; str() is the reason. status and
    latency_ms describe the last response when one came back."""

    def __init__(self, reason: str, *, status: int | None = None,
                 latency_ms: float | None = None) -> None:
        super().__init__(reason)
        self.status = status
        self.latency_ms = latency_ms


@dataclass(frozen=True)
class Verdict:
    passed: bool
    rationale: str
    status: int
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None


def ask_for_verdict(
        settings: JudgeSettings,
        system: str,
        prompt: str,
        *,
        transport: httpx.BaseTransport | None = None,
) -> Verdict:
    """Ask the judge model to record a verdict on `prompt`.

    Args:
        settings: Which model and how to call it; a check passes
            max_retries=0 for a single attempt.
        system: The system prompt.
        prompt: The user message: the rubric and the data to judge.
        transport: Test seam - an httpx transport (e.g. MockTransport) in
            place of the network. Production callers leave it unset.

    Raises:
        JudgeError: no judge configured, the key's variable unset, a
            non-retryable HTTP error, the retries exhausted, a refusal, or a
            reply that isn't a readable verdict.
    """
    if settings.provider is None or not settings.model:
        raise JudgeError("No judge configured: choose a provider and model under Settings")
    variable = settings.key_variable()
    key = environment_value(variable)
    if not key:
        raise JudgeError(f"{variable} is not set on this server")

    if settings.provider == JudgeProvider.anthropic:
        url, headers, body = _anthropic_request(settings, key, system, prompt)
        read = _read_anthropic
    else:
        url, headers, body = _openai_request(settings, key, system, prompt)
        read = _read_openai

    shown_url = _without_query(url)
    attempts = settings.max_retries + 1
    with httpx.Client(timeout=settings.timeout_seconds, transport=transport) as client:
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            retry_after = None
            status = latency_ms = None
            try:
                response = client.post(url, json=body, headers=headers)
            except httpx.TransportError as exc:
                reason = f"{type(exc).__name__} calling the judge at {shown_url}"
            else:
                latency_ms = round((time.perf_counter() - started) * 1000, 1)
                status = response.status_code
                if 200 <= status < 300:
                    verdict = read(response, latency_ms)
                    logger.info(
                        "Judge %s answered HTTP %d in %.1f ms (attempt %d of %d, %s in / %s "
                        "out tokens)",
                        settings.model, status, latency_ms, attempt, attempts,
                        verdict.input_tokens, verdict.output_tokens,
                        extra={"provider": settings.provider.value, "model": settings.model,
                               "status": status, "latency_ms": latency_ms,
                               "attempt": attempt, "input_tokens": verdict.input_tokens,
                               "output_tokens": verdict.output_tokens},
                    )
                    return verdict
                reason = (f"Judge answered HTTP {status} at {shown_url}"
                          f"{_provider_message(response)}")
                if status not in RETRYABLE_STATUSES:
                    logger.error("%s; not retried", reason,
                                 extra={"status": status, "attempt": attempt})
                    raise JudgeError(reason, status=status, latency_ms=latency_ms)
                retry_after = response.headers.get("Retry-After")

            if attempt == attempts:
                logger.error("%s after %d attempt(s); giving up", reason, attempt,
                             extra={"attempt": attempt, "attempts": attempts})
                raise JudgeError(f"{reason} after {attempt} attempt(s)",
                                 status=status, latency_ms=latency_ms)
            delay = _backoff(attempt, retry_after)
            logger.warning(
                "%s (attempt %d of %d); retrying in %.1f s", reason, attempt, attempts, delay,
                extra={"attempt": attempt, "attempts": attempts, "retry_in_seconds": delay},
            )
            time.sleep(delay)
    raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover


def _anthropic_request(settings: JudgeSettings, key: str, system: str,
                       prompt: str) -> tuple[str, dict, dict]:
    return (
        settings.endpoint_url(),
        {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
        {
            "model": settings.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [{"name": TOOL_NAME, "description": "Record your verdict on the answer.",
                       "input_schema": VERDICT_SCHEMA}],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        },
    )


def _openai_request(settings: JudgeSettings, key: str, system: str,
                    prompt: str) -> tuple[str, dict, dict]:
    return (
        settings.endpoint_url(),
        {"Authorization": f"Bearer {key}"},
        {
            "model": settings.model,
            "max_completion_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "verdict", "strict": True, "schema": VERDICT_SCHEMA,
            }},
        },
    )


def _read_anthropic(response: httpx.Response, latency_ms: float) -> Verdict:
    payload = _json(response, latency_ms)
    if payload.get("stop_reason") == "refusal":
        raise JudgeError("The judge refused to give a verdict", status=response.status_code,
                         latency_ms=latency_ms)
    blocks = payload.get("content") if isinstance(payload.get("content"), list) else []
    verdict = next((block.get("input") for block in blocks
                    if isinstance(block, dict) and block.get("type") == "tool_use"
                    and block.get("name") == TOOL_NAME), None)
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return _verdict(verdict, response.status_code, latency_ms,
                    usage.get("input_tokens"), usage.get("output_tokens"))


def _read_openai(response: httpx.Response, latency_ms: float) -> Verdict:
    payload = _json(response, latency_ms)
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise JudgeError("The judge's reply couldn't be read: no message in it",
                         status=response.status_code, latency_ms=latency_ms) from None
    if message.get("refusal"):
        raise JudgeError("The judge refused to give a verdict", status=response.status_code,
                         latency_ms=latency_ms)
    try:
        verdict = json.loads(message.get("content") or "")
    except json.JSONDecodeError:
        raise JudgeError("The judge's reply couldn't be read: its verdict isn't JSON",
                         status=response.status_code, latency_ms=latency_ms) from None
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return _verdict(verdict, response.status_code, latency_ms,
                    usage.get("prompt_tokens"), usage.get("completion_tokens"))


def _json(response: httpx.Response, latency_ms: float) -> dict:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        raise JudgeError("The judge's reply couldn't be read: it isn't a JSON object",
                         status=response.status_code, latency_ms=latency_ms)
    return payload


def _verdict(verdict: Any, status: int, latency_ms: float, input_tokens: Any,
             output_tokens: Any) -> Verdict:
    if not isinstance(verdict, dict):
        raise JudgeError("The judge's reply couldn't be read: no verdict in it",
                         status=status, latency_ms=latency_ms)
    passed, rationale = verdict.get("passed"), verdict.get("rationale")
    if not isinstance(passed, bool) or not isinstance(rationale, str) or not rationale.strip():
        raise JudgeError("The judge's reply couldn't be read: the verdict needs a true/false "
                         "`passed` and a `rationale`", status=status, latency_ms=latency_ms)
    return Verdict(
        passed=passed, rationale=rationale.strip(), status=status, latency_ms=latency_ms,
        input_tokens=input_tokens if isinstance(input_tokens, int) else None,
        output_tokens=output_tokens if isinstance(output_tokens, int) else None,
    )


def _without_query(url: str) -> str:
    """The URL as a reason shows it: a query string can carry a token."""
    return url.split("?", 1)[0].split("#", 1)[0]


def _provider_message(response: httpx.Response) -> str:
    """The provider's own error message, when it sent one: Anthropic and
    OpenAI both put it at error.message."""
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return ""
    if not isinstance(message, str) or not message.strip():
        return ""
    message = " ".join(message.split())
    if len(message) > _MAX_PROVIDER_MESSAGE:
        message = message[:_MAX_PROVIDER_MESSAGE - 1] + "…"
    return f": {message}"
