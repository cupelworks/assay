# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Exception handlers registered on the app.

Two jobs. For the errors FastAPI already turns into responses (HTTPException,
request validation), keep the response exactly as FastAPI builds it and just
leave a one-line reason in `scope["state"]["error_detail"]`, where
RequestContextMiddleware picks it up for the access line — so a 4xx is one
log line with its reason, not two. For anything unhandled, return a JSON 500
that carries the request ID, so whoever sees the error can quote an ID that
finds the traceback in the logs.

The traceback itself is not logged here: Starlette re-raises an unhandled
exception after the handler runs precisely so the server can log it, and
uvicorn does (through the app's logging config, request ID included).
Logging it here too would record every crash twice.
"""
from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from assay.logging_config import request_id_var
from assay.middleware import REQUEST_ID_HEADER

_MAX_DETAIL_CHARS = 500


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled_exception)


async def _http_exception(request: Request, exc: StarletteHTTPException) -> Response:
    request.state.error_detail = _summarize_detail(exc.detail)
    return await http_exception_handler(request, exc)


async def _validation_error(request: Request, exc: RequestValidationError) -> Response:
    # loc + msg only — an error's `input` echoes the submitted value, which
    # may be dataset or test content that doesn't belong in a log.
    request.state.error_detail = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    )[:_MAX_DETAIL_CHARS]
    return await request_validation_exception_handler(request, exc)


async def _unhandled_exception(request: Request, exc: Exception) -> Response:
    request_id = request_id_var.get()
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
        # RequestContextMiddleware doesn't see this response — it's sent by
        # ServerErrorMiddleware, outside every other middleware — so the
        # header is added here.
        headers={REQUEST_ID_HEADER: request_id} if request_id else None,
    )


def _summarize_detail(detail: object) -> str:
    """A string detail is logged as-is (capped). A structured one — e.g. the
    per-line list a rejected dataset file produces — is not: it embeds the
    submitted content, so only its size is logged.
    """
    if isinstance(detail, str):
        return detail[:_MAX_DETAIL_CHARS]
    if isinstance(detail, list):
        return f"<{len(detail)} structured error(s)>"
    return f"<{type(detail).__name__} detail>"
