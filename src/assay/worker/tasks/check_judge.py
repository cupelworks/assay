import uuid

from assay.logging_config import request_id_var
from assay.worker import app
from assay.worker.db import get_session
from assay.worker.services import check_judge as _check_judge_service


@app.task(bind=True)
def check_judge(self, check_id: uuid.UUID) -> None:  # pragma: no cover
    """Celery entry point for one check of the judge settings — the same
    shape as tasks/check_target.py: adopt the API call's request ID, acquire
    a real session, delegate to worker/services/check_judge.py, reset the
    context variable for the thread pool's next task.
    """
    request_token = request_id_var.set(self.request.get("request_id") or None)
    try:
        with get_session() as session:
            _check_judge_service(check_id, session)
    finally:
        request_id_var.reset(request_token)
