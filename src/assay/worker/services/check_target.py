import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from assay.messages import sentence
from assay.models import TargetCheckModel, TargetCheckStatus
from assay.schemas.settings import TargetSettings
from assay.worker import target

logger = logging.getLogger(__name__)

# A check still queued after this long is completed without calling the
# application: whoever asked has long stopped waiting for it, and a worker
# that starts after an outage shouldn't fire a backlog of stale calls.
CHECK_EXPIRES_AFTER = timedelta(minutes=5)


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
    claim = session.execute(
        update(TargetCheckModel)
        .where(TargetCheckModel.id == check_id,
               TargetCheckModel.status == TargetCheckStatus.pending)
        .values(status=TargetCheckStatus.running)
    )
    session.commit()
    if claim.rowcount == 0:
        # missing, or already claimed by another worker (a duplicate delivery)
        logger.info("Check %s not claimed: missing, already running or already completed",
                    check_id)
        return

    check = session.scalar(select(TargetCheckModel).where(TargetCheckModel.id == check_id))

    if _age(check.created_at) > CHECK_EXPIRES_AFTER:
        _complete(check, ok=False, error="Expired before a worker picked it up")
        session.commit()
        logger.warning("Check %s expired before a worker picked it up", check_id)
        return

    settings = TargetSettings.model_validate(check.settings).model_copy(
        update={"max_retries": 0}
    )
    try:
        response = target.get_answer(check.input, settings)
    except target.TargetError as exc:
        _complete(check, ok=False, error=sentence(str(exc)), status_code=exc.status,
                  latency_ms=exc.latency_ms)
    else:
        _complete(check, ok=True, answer=response.answer, status_code=response.status,
                  latency_ms=response.latency_ms)
    session.commit()

    logger.info(
        "Check %s completed: %s (%s)", check_id, "ok" if check.ok else "failed",
        f"HTTP {check.status_code}" if check.status_code else "no response",
        extra={"ok": check.ok, "status": check.status_code, "latency_ms": check.latency_ms},
    )


def _complete(check: TargetCheckModel, *, ok: bool, answer: str | None = None,
              error: str | None = None, status_code: int | None = None,
              latency_ms: float | None = None) -> None:
    check.status = TargetCheckStatus.completed
    check.ok = ok
    check.answer = answer
    check.error = error
    check.status_code = status_code
    check.latency_ms = latency_ms
    check.completed_at = datetime.now().astimezone()


def _age(created_at: datetime) -> timedelta:
    # SQLite hands a DateTime column back naive (local wall time); compare
    # like with like either way
    now = datetime.now(created_at.tzinfo) if created_at.tzinfo else datetime.now()
    return now - created_at
