"""Celery application for the run-execution worker.

The Celery app itself, configured against Redis (or the local SQLite
alternative) as broker and result backend, imported from the same
settings the API process already uses. Two tasks are registered:
`execute_run` (tasks/execute_run.py — runs one TestRunModel end to end; its
per-category evaluators are still stubs) and `reconcile_runs`
(tasks/reconcile_runs.py — the Beat-scheduled safety net that re-publishes
Pending runs whose original dispatch was lost).

The API process imports this app only to publish tasks by name
(services/runs/_common.py's _dispatch_runs); it never imports the task or
evaluator modules, and this never imports assay.main. Run with:

    celery -A assay.worker worker --loglevel=info
    celery -A assay.worker beat --loglevel=info   # exactly one per environment
"""
import ssl
from datetime import timedelta
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
    include=["assay.worker.tasks.execute_run", "assay.worker.tasks.reconcile_runs"],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    broker_use_ssl=_use_ssl_if_rediss(settings.celery_broker_url),
    redis_backend_use_ssl=_use_ssl_if_rediss(settings.celery_result_backend),
    # No task's return value is ever read back — outcomes live in the database.
    # Ignoring results also keeps .delay() from touching the result store before
    # publishing, so a publish can only fail with a KombuError (broker/encoding),
    # never the result store's generic RuntimeError. Note: app.send_task does NOT
    # read this setting — callers publishing by name must pass ignore_result=True.
    task_ignore_result=True,
    # Only read by a Beat process (celery -A assay.worker beat) — exactly one per
    # environment, or every tick gets published twice. The workers themselves
    # execute reconcile_runs like any other task.
    beat_schedule={
        "reconcile-runs": {
            "task": "assay.worker.tasks.reconcile_runs.reconcile_runs",
            "schedule": timedelta(minutes=settings.reconciliation_interval_minutes),
        },
    },
)
