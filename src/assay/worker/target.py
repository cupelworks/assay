"""The adapter that asks the application under test for an answer.

Assay tests an application, not a bare model, so when a test has no
recorded answer the run obtains one from the application's own endpoint,
treating it as a black box. The call is described by settings, not coded
per application - a URL, a method, headers, a JSON body template with
{{input}} where the prompt goes, and a JSONPath saying where the answer is
in the reply. The defaults are Assay's
own minimal contract (POST {"input": ...} -> {"output": ...}); an existing
application needs only the settings changed.

The settings are an argument, never read from the environment here: a run
passes the effective ones (saved from the UI, else the environment).
Error messages therefore name the setting, not an ASSAY_TARGET_* variable,
since the value may not have come from one.

Everything that can go wrong is a TargetError with a reason; execute_run
turns it into NotRan with that reason as the run's error. Retries cover
only failures that say "try again" (connection error, timeout, 5xx, 429),
with exponential backoff, honouring Retry-After when the application sends
one; any other 4xx is final on the first attempt, since a 400 or 401 won't
fix itself and a retry is a second, possibly billable, call.

Nothing here logs the prompt or the answer - only status, latency and
attempt counts. The per-call details belong in a per-run audit table once
one exists; until then the log line is their home.
"""
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from jsonpath_ng import parse as parse_jsonpath
from jsonpath_ng.exceptions import JSONPathError

from assay.schemas.settings import INPUT_PLACEHOLDER, TargetSettings

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({429}) | frozenset(range(500, 600))
MAX_RETRY_AFTER_SECONDS = 30.0

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class TargetError(Exception):
    """The application under test could not give an answer; str() is the reason.

    status and latency_ms describe the last response when one came back (an
    HTTP error, or a 2xx without a usable answer), and are None when nothing
    did (not configured, a connection error, a timeout), for a caller that
    reports what came back.
    """

    def __init__(self, reason: str, *, status: int | None = None,
                 latency_ms: float | None = None) -> None:
        super().__init__(reason)
        self.status = status
        self.latency_ms = latency_ms


@dataclass(frozen=True)
class TargetResponse:
    answer: str
    status: int
    latency_ms: float
    attempts: int


def get_answer(
        input_text: str,
        settings: TargetSettings,
        *,
        transport: httpx.BaseTransport | None = None,
) -> TargetResponse:
    """Call the application with the test's input and return its answer.

    Args:
        input_text: The test's input - substituted for every {{input}} in the
            body template's string values, never pasted into raw JSON, so
            quotes and newlines in a prompt can't break the request.
        settings: How to call the application and read its answer.
        transport: Test seam - an httpx transport (e.g. MockTransport) in
            place of the network. Production callers leave it unset.

    Raises:
        TargetError: no application configured, an unresolvable ${VAR} in a
            header, an invalid output path, a non-retryable HTTP error, the
            retries exhausted, a non-JSON reply, or no usable answer at the
            output path.
    """
    if not settings.url:
        raise TargetError("no application configured: no URL is set")
    headers = _resolve_headers(settings.headers)
    body = _render(settings.body, input_text)
    output_path = _compile_output_path(settings.output_path)

    attempts = settings.max_retries + 1
    with httpx.Client(timeout=settings.timeout_seconds, transport=transport) as client:
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            retry_after = None
            status = latency_ms = None
            try:
                response = client.request(
                    settings.method, settings.url, json=body, headers=headers,
                )
            except httpx.TransportError as exc:
                # ConnectError, ReadTimeout, RemoteProtocolError, ...: nothing
                # came back; the reason names the exception class, the
                # message itself can embed the URL, so it stays out.
                reason = f"{type(exc).__name__} calling the application"
            else:
                latency_ms = round((time.perf_counter() - started) * 1000, 1)
                status = response.status_code
                if 200 <= response.status_code < 300:
                    answer = _extract_answer(response, output_path, settings.output_path,
                                             latency_ms)
                    logger.info(
                        "Application answered HTTP %d in %.1f ms (attempt %d of %d)",
                        response.status_code, latency_ms, attempt, attempts,
                        extra={"status": response.status_code, "latency_ms": latency_ms,
                               "attempt": attempt, "attempts": attempts},
                    )
                    return TargetResponse(answer, response.status_code, latency_ms, attempt)
                reason = f"application answered HTTP {response.status_code}"
                if response.status_code not in RETRYABLE_STATUSES:
                    logger.error("%s; not retried", reason,
                                 extra={"status": response.status_code, "attempt": attempt})
                    raise TargetError(reason, status=status, latency_ms=latency_ms)
                retry_after = response.headers.get("Retry-After")

            if attempt == attempts:
                logger.error("%s after %d attempt(s); giving up", reason, attempt,
                             extra={"attempt": attempt, "attempts": attempts})
                raise TargetError(f"{reason} after {attempt} attempt(s)",
                                  status=status, latency_ms=latency_ms)
            delay = _backoff(attempt, retry_after)
            logger.warning(
                "%s (attempt %d of %d); retrying in %.1f s", reason, attempt, attempts, delay,
                extra={"attempt": attempt, "attempts": attempts, "retry_in_seconds": delay},
            )
            time.sleep(delay)


def _resolve_headers(headers: dict[str, str]) -> dict[str, str]:
    """Replace ${VAR} in header values from this process's environment, so a
    token lives in the environment rather than in the stored settings."""
    def substitute(match: re.Match) -> str:
        name = match.group(1)
        value = os.environ.get(name)
        if value is None:
            raise TargetError(
                f"a header references ${{{name}}} but {name} is not set on this server"
            )
        return value

    return {key: _ENV_REFERENCE.sub(substitute, value) for key, value in headers.items()}


def _render(template, input_text: str):
    """Walk the parsed JSON template and substitute {{input}} inside string
    values - structure, keys and non-string values are untouched."""
    if isinstance(template, str):
        return template.replace(INPUT_PLACEHOLDER, input_text)
    if isinstance(template, dict):
        return {key: _render(value, input_text) for key, value in template.items()}
    if isinstance(template, list):
        return [_render(item, input_text) for item in template]
    return template


def _compile_output_path(path: str):
    try:
        return parse_jsonpath(path)
    except JSONPathError as exc:
        raise TargetError(f"output path {path!r} is not a valid JSONPath: {exc}") from None


def _extract_answer(response: httpx.Response, output_path, path: str,
                    latency_ms: float) -> str:
    def fail(reason: str) -> TargetError:
        return TargetError(reason, status=response.status_code, latency_ms=latency_ms)

    try:
        payload = response.json()
    except ValueError:
        raise fail("application's reply is not JSON") from None
    matches = output_path.find(payload)
    if not matches:
        raise fail(f"nothing found at output path {path!r} in the application's reply")
    value = matches[0].value
    if not isinstance(value, str) or not value.strip():
        kind = "empty" if isinstance(value, str) else type(value).__name__
        raise fail(f"the value at output path {path!r} is {kind}, not a text answer")
    return value


def _backoff(attempt: int, retry_after: str | None) -> float:
    """1 s, then 2 s, then 4 s ... unless the application said when to come
    back (Retry-After as seconds or an HTTP date), capped so a hostile or
    misconfigured value can't park a worker thread for an hour."""
    delay = float(2 ** (attempt - 1))
    if retry_after:
        try:
            requested = float(retry_after)
        except ValueError:
            try:
                requested = (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError):
                requested = None
        if requested is not None and requested > 0:
            delay = requested
    return min(delay, MAX_RETRY_AFTER_SECONDS)
