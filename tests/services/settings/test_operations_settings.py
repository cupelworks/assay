# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""GET /settings/operations: the deployment's own settings, never a
connection string."""
import pytest
from fastapi.testclient import TestClient

from assay.config import settings
from assay.main import create_app
from assay.services.settings.get_operations_settings import get_operations_settings


@pytest.fixture
def deployed(monkeypatch):
    """Settings as a deployment behind the FE's proxy would have them."""
    for name, value in {
        "host": "0.0.0.0", "root_path": "/api", "cors_allowed_origins": [],
        "log_format": "json", "dataset_upload_max_mb": 25,
        "worker_db_pool_size": 4, "worker_db_max_overflow": 2,
        "reconciliation_interval_minutes": 30,
        "reconciliation_pending_threshold_minutes": 5,
        "database_url": "postgresql+asyncpg://assay:s3cret@db.internal:5432/assay",
        "celery_broker_url": "rediss://:k3y@cache.internal:6380/0",
        "celery_result_backend": "redis://:k3y@cache.internal:6379/1",
    }.items():
        monkeypatch.setattr(settings, name, value)


def test_every_value_is_the_one_the_api_runs_with(deployed):
    assert get_operations_settings().model_dump() == {
        "api": {"host": "0.0.0.0", "port": 8000, "root_path": "/api"},
        "cors_allowed_origins": [],
        "logging": {"level": "INFO", "format": "json"},
        "dataset_upload_max_mb": 25,
        "worker_db_pool": {"size": 4, "max_overflow": 2},
        "reconciliation": {"interval_minutes": 30, "pending_threshold_minutes": 5},
        "database": {"kind": "postgresql"},
        "broker": {"kind": "redis", "tls": True},
        "result_backend": {"kind": "redis", "tls": False},
    }


def test_no_connection_string_or_any_part_of_one_is_returned(deployed):
    body = get_operations_settings().model_dump_json()

    for secret_or_host in ("s3cret", "k3y", "db.internal", "cache.internal", "assay:",
                           "6380"):
        assert secret_or_host not in body


@pytest.mark.parametrize(("url", "kind", "tls"), [
    ("redis://localhost:6379/0", "redis", False),
    ("rediss://:key@host:6380/0", "redis", True),
    ("REDISS://host", "redis", True),
    ("sqla+sqlite:///./celery_broker.sqlite", "sqlite", False),
    ("db+sqlite:///./celery_results.sqlite", "sqlite", False),
    ("db+postgresql+psycopg2://u:p@h/d", "postgresql", False),
    ("amqps://u:p@h//", "amqp", True),
    ("memory://", "memory", False),
])
def test_a_queue_is_said_by_what_it_connects_to(monkeypatch, url, kind, tls):
    monkeypatch.setattr(settings, "celery_broker_url", url)

    assert get_operations_settings().broker.model_dump() == {"kind": kind, "tls": tls}


@pytest.mark.parametrize(("url", "kind"), [
    ("sqlite+aiosqlite:///./assay.db", "sqlite"),
    ("postgresql+asyncpg://u:p@h/d", "postgresql"),
])
def test_the_database_is_said_by_what_it_connects_to(monkeypatch, url, kind):
    monkeypatch.setattr(settings, "database_url", url)

    assert get_operations_settings().database.kind == kind


def test_the_endpoint_returns_them():
    response = TestClient(create_app()).get("/settings/operations")

    assert response.status_code == 200
    assert response.json() == get_operations_settings().model_dump(mode="json")
