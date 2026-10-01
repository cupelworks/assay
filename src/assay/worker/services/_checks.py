"""What a check of a settings group does on the worker, whichever group:
claim it atomically, expire it if it waited too long, write its outcome —
and if anything goes wrong once it's claimed, write that as its outcome
too, so a check never stays `running`."""
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from assay.messages import sentence
from assay.models import JudgeCheckModel, TargetCheckModel, TargetCheckStatus
from assay.schemas.settings import describe_validation_error

# A check still queued after this long is completed without calling out:
# whoever asked has long stopped waiting for it, and a worker that starts
# after an outage shouldn't fire a backlog of stale calls.
CHECK_EXPIRES_AFTER = timedelta(minutes=5)
EXPIRED = "Expired before a worker picked it up"

CheckModel = TargetCheckModel | JudgeCheckModel


def claim(session: Session, model: type[CheckModel], check_id) -> CheckModel | None:
    """The check, moved from pending to running by this worker — or None
    when it's missing, or already claimed (a duplicate delivery)."""
    claimed = session.execute(
        update(model)
        .where(model.id == check_id, model.status == TargetCheckStatus.pending)
        .values(status=TargetCheckStatus.running)
    )
    session.commit()
    if claimed.rowcount == 0:
        return None
    return session.scalar(select(model).where(model.id == check_id))


def expired(check: CheckModel) -> bool:
    created_at = check.created_at
    # SQLite hands a DateTime column back naive (local wall time); compare
    # like with like either way
    now = datetime.now(created_at.tzinfo) if created_at.tzinfo else datetime.now()
    return now - created_at > CHECK_EXPIRES_AFTER


def complete(check: CheckModel, *, ok: bool, answer: str | None = None,
             error: str | None = None, status_code: int | None = None,
             latency_ms: float | None = None) -> None:
    check.status = TargetCheckStatus.completed
    check.ok = ok
    check.answer = answer
    check.error = error
    check.status_code = status_code
    check.latency_ms = latency_ms
    check.completed_at = datetime.now().astimezone()


def fail(session: Session, check: CheckModel, exc: Exception) -> None:
    """Complete a claimed check that broke on the way — settings this code
    can't read, a database error, a bug — with the reason, instead of leaving
    it `running` for good."""
    session.rollback()
    reason = (describe_validation_error(exc) if isinstance(exc, ValidationError)
              else str(exc) or type(exc).__name__)
    complete(check, ok=False, error=sentence(f"The check failed on the worker: {reason}"))
    session.commit()
