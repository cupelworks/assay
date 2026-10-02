"""Stop a batch midway (docs/statistics/dev_notes.md note 16): every run
still Pending becomes Not Ran, in one statement; runs already executing
finish and count. The batch is then Incomplete once none is running."""
import logging
import uuid
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import StatisticalBatchModel, TestRunModel, TestStatus
from assay.schemas.statistics import BatchDetails
from assay.services.statistics._batches import STOP_REASON, describe, find_batch_or_404, refresh

logger = logging.getLogger(__name__)


async def stop_batch(batch_id: uuid.UUID, session: AsyncSession) -> BatchDetails:
    """Cancel the batch's pending runs and return the batch.

    Safe against the workers with no new coordination: a worker claims a run
    only while it's Pending, in one conditional UPDATE, so a run cancelled
    first is never claimed (its task message, when it arrives, is ignored as
    a duplicate delivery is), and a run claimed first simply finishes. The
    reconciliation scan re-publishes only Pending runs, so cancelled runs
    stay cancelled. A batch with nothing left pending — finished, or only
    running runs left — is returned unchanged: stopping it changes nothing.

    Raises: HTTPException 404 for an unknown batch.
    """
    batch = await find_batch_or_404(batch_id, session)
    now = datetime.now().astimezone()
    cancelled = (await session.execute(
        update(TestRunModel)
        .where(TestRunModel.batch_id == batch.id, TestRunModel.status == TestStatus.pending)
        .values(status=TestStatus.not_ran, error=STOP_REASON, executed_at=now)
    )).rowcount
    if cancelled:
        await session.execute(
            update(StatisticalBatchModel)
            .where(StatisticalBatchModel.id == batch.id,
                   StatisticalBatchModel.stopped_at.is_(None))
            .values(stopped_at=now))
    await session.commit()
    await session.refresh(batch)
    if cancelled:
        logger.info("Stopped batch %s: %d pending runs cancelled", batch.id, cancelled,
                    extra={"batch_id": batch.id, "run_count": cancelled})
    await refresh(batch, session)
    return await describe(batch, session)
