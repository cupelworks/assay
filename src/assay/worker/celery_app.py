"""Celery application for the run-execution worker.

This is infrastructure only — the Celery app itself, configured against
Redis as both broker and result backend, imported from the same settings
the API process already uses. No tasks are registered yet: what a task
actually does (call a model, resolve config_fields per test type, write
back TestRunModel.results/error/status) depends on the evaluator design
in docs/run_execution/dev_notes.md, which isn't decided yet. Task modules
land here once that is.

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
