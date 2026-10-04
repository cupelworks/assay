"""List batches, newest first, optionally by scope and status."""
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import IN_PROGRESS_BATCH_STATUSES, BatchStatus, StatisticalBatchModel
from assay.schemas.statistics import BatchList, BatchStatusName
from assay.services.statistics._batches import describe, refresh, scope_names


async def list_batches(session: AsyncSession, *, offset: int, limit: int,
                       test_id: uuid.UUID | None = None, test_set_id: uuid.UUID | None = None,
                       test_plan_id: uuid.UUID | None = None,
                       status: BatchStatusName | None = None) -> BatchList:
    """The in-progress batches of the scope asked for are brought up to date
    first — one small count each — so a filter on status sees the truth, and a
    batch that finished since the last read gets its result here. Batches of
    other scopes are left for their own reads."""
    scope = []
    for column, value in ((StatisticalBatchModel.test_id, test_id),
                          (StatisticalBatchModel.test_set_id, test_set_id),
                          (StatisticalBatchModel.test_plan_id, test_plan_id)):
        if value is not None:
            scope.append(column == value)
    in_progress = (await session.scalars(
        select(StatisticalBatchModel)
        .where(StatisticalBatchModel.status.in_(IN_PROGRESS_BATCH_STATUSES), *scope))).all()
    for batch in in_progress:
        await refresh(batch, session)

    filters = list(scope)
    if status is not None:
        filters.append(StatisticalBatchModel.status == BatchStatus(status.value))

    total = await session.scalar(
        select(func.count()).select_from(StatisticalBatchModel).where(*filters))
    page = (await session.scalars(
        select(StatisticalBatchModel).where(*filters)
        .order_by(StatisticalBatchModel.created_at.desc(), StatisticalBatchModel.id)
        .offset(offset).limit(limit))).all()
    names = await scope_names(list(page), session)
    items = [await describe(batch, session, with_result=False, names=names) for batch in page]
    return BatchList(items=items, total=total, offset=offset, limit=limit)
