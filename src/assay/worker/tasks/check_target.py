import uuid

from assay.logging_config import request_id_var
from assay.worker import app
from assay.worker.db import get_session
from assay.worker.services import check_target as _check_target_service


@app.task(bind=True)
def check_target(self, check_id: uuid.UUID) -> None:  # pragma: no cover
    """Celery entry point for one check of the application-under-test
    settings — adopts the request ID of the API call that created the check
    (so both sides' log lines share it), acquires a real session and
    delegates to worker/services/check_target.py, which is what's unit
    tested. Same shape and reasoning as tasks/execute_run.py, including
    resetting the context variable for the thread pool's next task.
    """
    request_token = request_id_var.set(self.request.get("request_id") or None)
    try:
        with get_session() as session:
            _check_target_service(check_id, session)
    finally:
        request_id_var.reset(request_token)
