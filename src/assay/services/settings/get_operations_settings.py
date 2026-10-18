# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

from assay.config import settings
from assay.schemas import OperationsSettings

# The schemes that encrypt their connection, and the kind each one is
_TLS_SCHEMES = {"rediss": "redis", "amqps": "amqp"}
# Celery's prefixes for a SQLAlchemy-backed broker and result backend
_CELERY_PREFIXES = ("sqla+", "db+")


def get_operations_settings() -> OperationsSettings:
    """How this deployment is set up, as the API process read it from its
    environment at start-up. A connection string is never returned, not
    even its host: only what it connects to."""
    return OperationsSettings.model_validate(
        {
            "api": {"host": settings.host, "port": settings.port, "root_path": settings.root_path},
            "cors_allowed_origins": settings.cors_allowed_origins,
            "logging": {"level": settings.log_level, "format": settings.log_format},
            "dataset_upload_max_mb": settings.dataset_upload_max_mb,
            "worker_db_pool": {
                "size": settings.worker_db_pool_size,
                "max_overflow": settings.worker_db_max_overflow,
            },
            "reconciliation": {
                "interval_minutes": settings.reconciliation_interval_minutes,
                "pending_threshold_minutes": settings.reconciliation_pending_threshold_minutes,
            },
            "database": {"kind": make_url(settings.database_url).get_backend_name()},
            "broker": _queue_connection(settings.celery_broker_url),
            "result_backend": _queue_connection(settings.celery_result_backend),
        }
    )


def _queue_connection(url: str) -> dict:
    """`redis://` → redis, `rediss://` → redis over TLS, `sqla+sqlite://` →
    sqlite: the scheme without Celery's prefix or a driver."""
    scheme = urlsplit(url).scheme.lower()
    for prefix in _CELERY_PREFIXES:
        scheme = scheme.removeprefix(prefix)
    kind = scheme.split("+", 1)[0]
    return {"kind": _TLS_SCHEMES.get(kind, kind), "tls": kind in _TLS_SCHEMES}
