# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""RequestContextMiddleware + the exception handlers, end to end through the
real app. The routes added here are test-only: none of them touch the
database, so the suite stays hermetic.
"""
import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from assay.main import create_app


@pytest.fixture
def client():
    app = create_app()

    @app.get("/__ok")
    def ok():
        return {"ok": True}

    @app.get("/__typed")
    def typed(n: int):
        return {"n": n}

    @app.get("/__conflict")
    def conflict():
        raise HTTPException(status_code=409, detail="already exists")

    @app.get("/__structured")
    def structured():
        raise HTTPException(status_code=422, detail=[{"line": 1, "content": "secret content"}])

    @app.get("/__boom")
    def boom():
        raise RuntimeError("kaboom")

    return TestClient(app, raise_server_exceptions=False)


def _access_record(caplog):
    records = [record for record in caplog.records if record.name == "assay.access"]
    assert len(records) == 1, [record.getMessage() for record in records]
    return records[0]


# --- request id ---


def test_every_response_carries_a_generated_request_id(client):
    response = client.get("/__ok")

    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32
    int(request_id, 16)  # a uuid4 hex


def test_a_safe_caller_supplied_request_id_is_reused_and_echoed(client):
    response = client.get("/__ok", headers={"X-Request-ID": "fe-abc.123:7"})

    assert response.headers["X-Request-ID"] == "fe-abc.123:7"


@pytest.mark.parametrize("unsafe", ["has space", "x" * 129, "semi;colon"])
def test_an_unsafe_caller_supplied_request_id_is_replaced(client, unsafe):
    response = client.get("/__ok", headers={"X-Request-ID": unsafe})

    assert response.headers["X-Request-ID"] != unsafe
    assert len(response.headers["X-Request-ID"]) == 32


def test_the_browser_may_read_the_request_id_header_cross_origin(client):
    response = client.get("/__ok", headers={"Origin": "http://localhost:4200"})

    assert "X-Request-ID" in response.headers["Access-Control-Expose-Headers"]


# --- access line ---


def test_success_is_one_info_line_with_structured_fields(client, caplog):
    with caplog.at_level(logging.INFO, logger="assay.access"):
        response = client.get("/__ok")

    record = _access_record(caplog)
    assert record.levelno == logging.INFO
    assert record.getMessage().startswith("GET /__ok -> 200 (")
    assert record.request_id == response.headers["X-Request-ID"]
    assert record.method == "GET"
    assert record.path == "/__ok"
    assert record.route == "/__ok"
    assert record.status_code == 200
    assert isinstance(record.duration_ms, float)
    assert not hasattr(record, "error")


def test_health_is_logged_at_debug_not_info(client, caplog):
    with caplog.at_level(logging.DEBUG, logger="assay.access"):
        client.get("/health")

    assert _access_record(caplog).levelno == logging.DEBUG


def test_route_template_is_logged_alongside_the_concrete_path(client, caplog):
    with caplog.at_level(logging.INFO, logger="assay.access"):
        client.get("/test-sets/00000000-0000-0000-0000-000000000000/entries/not-a-uuid")

    record = _access_record(caplog)
    assert record.path == "/test-sets/00000000-0000-0000-0000-000000000000/entries/not-a-uuid"
    assert record.route == "/test-sets/{test_set_id}/entries/{entry_id}"


def test_a_4xx_is_one_warning_line_carrying_the_detail(client, caplog):
    with caplog.at_level(logging.INFO, logger="assay.access"):
        client.get("/__conflict")

    record = _access_record(caplog)
    assert record.levelno == logging.WARNING
    assert record.status_code == 409
    assert record.error == "already exists"
    assert "error='already exists'" in record.getMessage()


def test_an_unknown_path_is_a_warning_with_the_router_detail(client, caplog):
    with caplog.at_level(logging.INFO, logger="assay.access"):
        client.get("/__missing")

    record = _access_record(caplog)
    assert record.levelno == logging.WARNING
    assert record.status_code == 404
    assert record.error == "Not Found"
    assert not hasattr(record, "route")  # nothing matched, no template to report


def test_a_validation_error_is_summarized_without_echoing_the_input(client, caplog):
    with caplog.at_level(logging.INFO, logger="assay.access"):
        response = client.get("/__typed", params={"n": "not-a-number"})

    assert response.status_code == 422
    record = _access_record(caplog)
    assert record.levelno == logging.WARNING
    assert record.error.startswith("query.n: ")
    assert "not-a-number" not in record.error


def test_a_structured_detail_is_not_logged_verbatim(client, caplog):
    with caplog.at_level(logging.INFO, logger="assay.access"):
        client.get("/__structured")

    record = _access_record(caplog)
    assert record.error == "<1 structured error(s)>"
    assert "secret content" not in record.getMessage()


# --- unhandled exception ---


def test_an_unhandled_exception_is_a_json_500_carrying_the_request_id(client, caplog):
    with caplog.at_level(logging.INFO, logger="assay.access"):
        response = client.get("/__boom")

    request_id = response.headers["X-Request-ID"]
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error", "request_id": request_id}

    record = _access_record(caplog)
    assert record.levelno == logging.ERROR
    assert record.status_code == 500
    assert record.error == "RuntimeError: kaboom"
    assert record.request_id == request_id
