# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import uuid

from assay.logging_config import request_id_var, run_id_var
from assay.worker import app
from assay.worker.db import get_session
from assay.worker.services import execute_run as _execute_run_service
from assay.worker.services.advance_batch import batch_to_advance


@app.task(bind=True)
def execute_run(self, run_id: uuid.UUID) -> None:  # pragma: no cover
    """Celery entry point — adopts the request ID the API put in the task
    message (so this run's log lines carry the same ID as the request that
    created it; a run re-published by the reconciliation scan has none),
    sets the run ID for the same purpose, acquires a real session and
    delegates. Thin on purpose, same as an API route handler: the actual
    logic (worker/services/execute_run.py) takes session as a parameter and
    is what's unit tested, not this wrapper.

    bind=True is what makes Celery pass the task object as `self`. It's
    needed for one thing: `self.request`, the context of this execution,
    which is where the message's headers — the request ID — can be read.
    Callers are unaffected: `.delay(run_id)` and the API's
    `send_task(args=[run_id])` never pass `self`, Celery injects it.

    Both context variables are reset afterwards: a thread pool reuses its
    threads, so unlike an asyncio task the context would otherwise leak into
    the next task on the same thread.
    """
    request_token = request_id_var.set(self.request.get("request_id") or None)
    run_token = run_id_var.set(str(run_id))
    try:
        with get_session() as session:
            _execute_run_service(run_id, session)
            # the last run of a wave of a batch that runs until there's an
            # answer: decide on the next wave
            batch_id = batch_to_advance(run_id, session)
        if batch_id is not None:
            from assay.worker.tasks.advance_batch import advance_batch
            advance_batch.delay(batch_id)
    finally:
        run_id_var.reset(run_token)
        request_id_var.reset(request_token)
