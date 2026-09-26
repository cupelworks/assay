from datetime import timedelta

from assay.config import settings
from assay.worker import app
from assay.worker.db import get_session
from assay.worker.services import reconcile_pending_runs
from assay.worker.tasks.execute_run import execute_run


@app.task()
def reconcile_runs() -> None:  # pragma: no cover
    """Celery Beat entry point — acquires a real session and delegates. Thin on
    purpose, same as execute_run's wrapper: the logic lives in
    worker/services/reconcile_runs.py, which is what's unit tested.
    """
    threshold = timedelta(minutes=settings.reconciliation_pending_threshold_minutes)
    with get_session() as session:
        reconcile_pending_runs(session, threshold, publish=execute_run.delay)
