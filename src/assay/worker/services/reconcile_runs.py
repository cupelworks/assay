import logging
import uuid
from collections.abc import Callable
from datetime import timedelta

from kombu.exceptions import KombuError
from sqlalchemy import select
from sqlalchemy.orm import Session

from assay.models import TestRunModel, TestStatus
from assay.timestamps import utc_now

logger = logging.getLogger(__name__)


def reconcile_pending_runs(
        session: Session,
        threshold: timedelta,
        publish: Callable[[uuid.UUID], object],
) -> None:
    """Re-publishes execute_run for every Pending run older than `threshold` —
    the safety net for a run whose original dispatch never reached the broker.

    Re-publishing a run that's actually still queued is safe: the duplicate
    task's atomic claim matches zero rows and no-ops. Each publish is isolated
    in its own try/except KombuError, so one broker failure never stops the
    rest; anything that isn't a KombuError is a bug, not a publish failure,
    and propagates (failing this scan visibly — the next one retries).

    Args:
        session: Active sync SQLAlchemy session (assay.worker.db).
        threshold: Minimum age of a Pending run before it's re-published.
        publish: Publishes execute_run for one run ID — `execute_run.delay`,
            passed in by the task wrapper so this module never imports the
            tasks package (which imports this one).
    """
    cutoff = utc_now() - threshold
    stale_ids = session.scalars(
        select(TestRunModel.id).where(
            TestRunModel.status == TestStatus.pending,
            TestRunModel.created_at < cutoff,
        )
    ).all()

    threshold_minutes = round(threshold.total_seconds() / 60)
    if not stale_ids:
        logger.debug(
            "Reconciliation scan: no Pending runs older than %d min", threshold_minutes,
            extra={"threshold_minutes": threshold_minutes},
        )
        return

    republished = 0
    for run_id in stale_ids:
        try:
            publish(run_id)
        except KombuError:
            logger.exception("Failed to re-publish execute_run for run %s", run_id)
        else:
            republished += 1

    logger.info(
        "Reconciliation scan: re-published %d of %d Pending runs older than %d min",
        republished, len(stale_ids), threshold_minutes,
        extra={
            "republished": republished,
            "stale_count": len(stale_ids),
            "threshold_minutes": threshold_minutes,
        },
    )
