# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""ASSAY_ROOT_PATH: the API behind a proxy that serves it under a prefix
and removes it before passing a request on."""
import pytest
from fastapi.testclient import TestClient

from assay.config import settings
from assay.main import create_app


@pytest.fixture
def behind_a_proxy(monkeypatch) -> TestClient:
    monkeypatch.setattr(settings, "root_path", "/api")
    return TestClient(create_app())


def test_the_docs_load_the_schema_under_the_prefix(behind_a_proxy):
    response = behind_a_proxy.get("/docs")

    assert response.status_code == 200
    assert "'/api/openapi.json'" in response.text


def test_the_schema_sends_its_requests_under_the_prefix(behind_a_proxy):
    assert behind_a_proxy.get("/openapi.json").json()["servers"] == [{"url": "/api"}]


@pytest.mark.parametrize("path", ["/health", "/api/health"])
def test_a_request_is_routed_with_the_prefix_removed_or_still_there(behind_a_proxy, path):
    assert behind_a_proxy.get(path).json() == {"status": "ok"}


def test_without_a_root_path_the_schema_names_no_server():
    assert "servers" not in TestClient(create_app()).get("/openapi.json").json()
