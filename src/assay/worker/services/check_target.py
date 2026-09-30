import logging
import uuid

from sqlalchemy.orm import Session

from assay.messages import sentence
from assay.models import TargetCheckModel
from assay.schemas.settings import TargetSettings
from assay.worker import target
from assay.worker.services._checks import CHECK_EXPIRES_AFTER, EXPIRED, claim, complete, expired

logger = logging.getLogger(__name__)

__all__ = ["CHECK_EXPIRES_AFTER", "check_target"]


def check_target(check_id: uuid.UUID, session: Session) -> None:
    """Runs one check of the application-under-test settings: claim it, make
    one call with the settings stored on the check, write the outcome.

    The settings come from the check's own row, never from what's saved when
    the worker gets here — a check reports on what the user asked about,
    including proposed settings that were never saved. One attempt, no
    retries: someone is waiting on the answer.

    Args:
        check_id: UUID of the TargetCheckModel to run.
        session: Active sync SQLAlchemy session (assay.worker.db).
    """
    check = claim(session, TargetCheckModel, check_id)
    if check is None:
        logger.info("Check %s not claimed: missing, already running or already completed",
                    check_id)
        return

    if expired(check):
        complete(check, ok=False, error=EXPIRED)
        session.commit()
        logger.warning("Check %s expired before a worker picked it up", check_id)
        return

    settings = TargetSettings.model_validate(check.settings).model_copy(
        update={"max_retries": 0}
    )
    try:
        response = target.get_answer(check.input, settings)
    except target.TargetError as exc:
        complete(check, ok=False, error=sentence(str(exc)), status_code=exc.status,
                 latency_ms=exc.latency_ms)
    else:
        # A run scores an empty answer; a check reports it, since its question
        # is whether these settings get a usable answer out of the application.
        complete(check, ok=not response.empty, answer=response.answer or None,
                 error=response.empty, status_code=response.status,
                 latency_ms=response.latency_ms)
    session.commit()

    logger.info(
        "Check %s completed: %s (%s)", check_id, "ok" if check.ok else "failed",
        f"HTTP {check.status_code}" if check.status_code else "no response",
        extra={"ok": check.ok, "status": check.status_code, "latency_ms": check.latency_ms},
    )
