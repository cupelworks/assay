"""Celery application for the run-execution worker.

The Celery app itself, configured against Redis (or the local SQLite
alternative) as broker and result backend, imported from the same
settings the API process already uses. `execute_run` (tasks/execute_run.py)
is the one task registered so far — what it actually does (resolving a
run's content and assigned test types, calling the right evaluator per
type, writing back results/error/status) is still stubbed; see
docs/run_execution/dev_notes.md notes 4/5 for what's decided and what
isn't yet.

Entirely separate from the API process — assay.main never imports this,
and this never imports assay.main. Run with:

    celery -A assay.worker worker --loglevel=info
"""
import ssl
from urllib.parse import urlparse

from celery import Celery

from assay.config import settings


def _use_ssl_if_rediss(url: str) -> dict | None:
    """kombu's redis transport recognizes a rediss:// URL and switches to TLS
    on its own, but its default ssl_cert_reqs is CERT_NONE — TLS with no
    certificate verification, which defeats most of the point (e.g. a
    managed broker like Azure Cache for Redis, which requires rediss:// by
    default). Returns config forcing verification for a rediss:// URL, or
    None for a plain redis:// one (unauthenticated local dev, left as-is).
    """
    if urlparse(url).scheme != "rediss":
        return None
    return {"ssl_cert_reqs": ssl.CERT_REQUIRED}


app = Celery(
    "assay",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    # Every task module the worker needs to import to register its tasks —
    # a task only exists on `app` once its @app.task()-decorated module has
    # actually been imported. New task modules join this list, not just
    # tasks/__init__.py's own __all__ (that re-export is for ergonomic
    # access to assay.worker.tasks.execute_run, it doesn't by itself make
    # Celery import anything at worker startup).
    include=["assay.worker.tasks.execute_run"],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_use_ssl=_use_ssl_if_rediss(settings.celery_broker_url),
    redis_backend_use_ssl=_use_ssl_if_rediss(settings.celery_result_backend),
)
