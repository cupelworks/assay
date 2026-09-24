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
from celery import Celery

from assay.config import settings

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
)
