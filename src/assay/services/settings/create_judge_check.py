import logging
import uuid
from datetime import datetime

from kombu.exceptions import KombuError
from sqlalchemy.ext.asyncio import AsyncSession

from assay.judge_settings import resolve_judge_settings
from assay.logging_config import request_id_var
from assay.models import JudgeCheckModel, TargetCheckStatus
from assay.schemas import JudgeCheck, JudgeCheckRequest, JudgeSettings, JudgeSettingsUpdate
from assay.services.settings._common import _apply_or_422, _find_judge_row, _judge_check_schema
from assay.services.settings.create_target_check import NOT_DISPATCHED
from assay.worker import app as _celery_app

logger = logging.getLogger(__name__)

CHECK_TASK = "assay.worker.tasks.check_judge.check_judge"


async def create_judge_check(
        request: JudgeCheckRequest,
        session: AsyncSession,
) -> JudgeCheck:
    """Start a check of the judge settings, run on a worker, exactly like a
    check of the application-under-test settings: the settings in effect
    with any proposed fields applied, validated like a PATCH and stored on
    the check — never saved — then published to the worker by task name. A
    check that can't be published is completed on the spot with that reason.

    Args:
        request: Any proposed settings.
        session: Active async database session.

    Returns:
        The check, `pending` (or `completed` if it couldn't be sent).

    Raises:
        RequestValidationError: 422 listing every problem with the proposed
            settings. No check is created.
    """
    settings = _apply_or_422(
        resolve_judge_settings(await _find_judge_row(session)),
        request.settings or JudgeSettingsUpdate(),
        loc=("body", "settings"),
        schema=JudgeSettings,
    )

    check = JudgeCheckModel(
        id=uuid.uuid4(),
        created_at=datetime.now().astimezone(),
        status=TargetCheckStatus.pending,
        settings=settings.model_dump(mode="json"),
    )
    session.add(check)
    await session.commit()

    proposed = sorted(request.settings.model_fields_set) if request.settings else []
    try:
        _celery_app.send_task(
            CHECK_TASK,
            args=[check.id],
            ignore_result=True,
            headers={"request_id": request_id_var.get()},
        )
    except KombuError:
        logger.exception("Failed to dispatch judge check %s", check.id)
        check.status = TargetCheckStatus.completed
        check.ok = False
        check.error = NOT_DISPATCHED
        check.completed_at = datetime.now().astimezone()
        await session.commit()
    else:
        logger.info(
            "Created check %s of the judge settings (proposed: %s)",
            check.id, ", ".join(proposed) or "none",
            extra={"check_id": check.id, "proposed_fields": proposed},
        )
    return _judge_check_schema(check)
