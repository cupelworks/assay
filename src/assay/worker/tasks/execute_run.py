import uuid

from assay.worker import app
from assay.worker.db import get_session
from assay.worker.services import execute_run as _execute_run_service


@app.task()
def execute_run(run_id: uuid.UUID) -> None:  # pragma: no cover
    """Celery entry point — acquires a real session and delegates
    immediately. Thin on purpose, same as an API route handler: the actual
    logic (worker/services/execute_run.py) takes session as a parameter and
    is what's unit tested, not this wrapper.
    """
    with get_session() as session:
        _execute_run_service(run_id, session)
