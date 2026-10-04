import logging
import uuid

from kombu.exceptions import KombuError
from sqlalchemy.ext.asyncio import AsyncSession

from assay.logging_config import request_id_var
from assay.models import TargetCheckModel, TargetCheckStatus
from assay.schemas import TargetCheck, TargetCheckRequest, TargetSettingsUpdate
from assay.services.settings._common import (
    _apply_or_422,
    _find_target_row,
    _target_check_schema,
)
from assay.target_settings import resolve_target_settings
from assay.timestamps import utc_now
from assay.worker import app as _celery_app

logger = logging.getLogger(__name__)

CHECK_TASK = "assay.worker.tasks.check_target.check_target"
NOT_DISPATCHED = "Could not be sent to a worker"


async def create_target_check(
        request: TargetCheckRequest,
        session: AsyncSession,
) -> TargetCheck:
    """Start a check of the application-under-test settings, run on a worker.

    The settings checked are the ones in effect with any proposed fields
    applied, validated like a PATCH and stored on the check itself — never
    saved as the settings. The check is committed, then published to the
    worker by task name, exactly like a run (services/runs/_common.py's
    _dispatch_runs): the API never imports the worker's task.

    A check that can't be published is completed on the spot with that
    reason — there's no reconciliation scan for checks, asking again is the
    retry — so the caller always gets back a check that either a worker
    will complete or that already is.

    Args:
        request: The input to send and any proposed settings.
        session: Active async database session.

    Returns:
        The check, `pending` (or `completed` if it couldn't be sent).

    Raises:
        RequestValidationError: 422 listing every problem with the proposed
            settings. No check is created.
    """
    settings = _apply_or_422(
        resolve_target_settings(await _find_target_row(session)),
        request.settings or TargetSettingsUpdate(),
        loc=("body", "settings"),
    )

    check = TargetCheckModel(
        id=uuid.uuid4(),
        created_at=utc_now(),
        status=TargetCheckStatus.pending,
        input=request.input,
        settings=settings.model_dump(mode="json"),
    )
    session.add(check)
    await session.commit()

    proposed = sorted(request.settings.model_fields_set) if request.settings else []
    try:
        _celery_app.send_task(
            CHECK_TASK,
            args=[check.id],
            # see _dispatch_runs: send_task ignores task_ignore_result
            ignore_result=True,
            headers={"request_id": request_id_var.get()},
        )
    except KombuError:
        logger.exception("Failed to dispatch check %s", check.id)
        check.status = TargetCheckStatus.completed
        check.ok = False
        check.error = NOT_DISPATCHED
        check.completed_at = utc_now()
        await session.commit()
    else:
        logger.info(
            "Created check %s of the application-under-test settings (proposed: %s)",
            check.id, ", ".join(proposed) or "none",
            extra={"check_id": check.id, "proposed_fields": proposed},
        )
    return _target_check_schema(check)
