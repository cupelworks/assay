import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from assay.logging_config import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"

# Dedicated logger name, like uvicorn.access — lets a log pipeline route or
# filter access lines separately from the app's own events.
logger = logging.getLogger("assay.access")

# A caller-supplied ID is only honoured if it can't smuggle anything into a
# log line (no whitespace, no control characters) and stays short.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
# Polled continuously by the platform — DEBUG, or they'd be most of the log.
_QUIET_PATHS = frozenset({"/health", "/metrics"})


class RequestContextMiddleware:
    """Gives every request an ID and logs one access line per request.

    The ID comes from the caller's X-Request-ID header when it's safe to
    reuse (so a client can correlate its own logs with ours), otherwise it's
    generated. It's set in a context variable for the rest of the request —
    every log record picks it up (see assay.logging_config) — and echoed back
    in the response's X-Request-ID header. No reset is needed at the end:
    the server runs each request in its own asyncio task, and a task never
    shares its context with the next one.

    The access line is emitted once the response is done: INFO for a
    success, WARNING for a 4xx, ERROR for a 5xx (DEBUG for the polled
    /health and /metrics), with the route template, status and duration as
    structured fields. When the request failed, the line also carries the
    reason — the exception, for an unhandled one, or the response's detail,
    which the exception handlers leave in `scope["state"]` for this purpose
    — so one line answers "what happened" without a second lookup.

    Plain ASGI rather than BaseHTTPMiddleware: the latter buffers streaming
    responses and swallows the context of anything that runs after the
    handler returns.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request_id_var.set(request_id)

        started = time.perf_counter()
        # An exception before any response was started is answered with a
        # 500 further out (Starlette's ServerErrorMiddleware) — that's the
        # status to report unless a response start says otherwise.
        status_code = 500
        error: str | None = None

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", [])
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._log_access(scope, status_code, error, time.perf_counter() - started)

    @staticmethod
    def _log_access(scope: Scope, status_code: int, error: str | None, elapsed: float) -> None:
        method, path = scope["method"], scope["path"]
        duration_ms = round(elapsed * 1000, 1)
        error = error or (scope.get("state") or {}).get("error_detail")

        if status_code >= 500:
            level = logging.ERROR
        elif status_code >= 400:
            level = logging.WARNING
        elif path in _QUIET_PATHS:
            level = logging.DEBUG
        else:
            level = logging.INFO

        fields = {
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
        }
        route = scope.get("route")
        if route is not None:
            fields["route"] = route.path
        client = scope.get("client")
        if client:
            fields["client_ip"] = client[0]

        if error is None:
            logger.log(level, "%s %s -> %d (%.1f ms)", method, path, status_code, duration_ms,
                       extra=fields)
        else:
            logger.log(level, "%s %s -> %d (%.1f ms) error=%r", method, path, status_code,
                       duration_ms, error, extra={**fields, "error": error})
