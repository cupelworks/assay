import uuid

from assay.logging_config import request_id_var
from assay.worker import app
from assay.worker.db import get_session
from assay.worker.services.advance_batch import advance_batch as _advance_batch_service


@app.task(bind=True)
def advance_batch(self, batch_id: uuid.UUID) -> None:  # pragma: no cover
    """Celery entry point: after a wave of a batch that runs until there's an
    answer, run the next wave or stop. Thin on purpose, like execute_run: the
    logic (worker/services/advance_batch.py) takes the session and the
    publisher as parameters and is what's tested."""
    from assay.worker.tasks.execute_run import execute_run

    token = request_id_var.set(self.request.get("request_id") or None)
    try:
        with get_session() as session:
            _advance_batch_service(uuid.UUID(str(batch_id)), session,
                                   lambda run_id: execute_run.delay(run_id))
    finally:
        request_id_var.reset(token)
