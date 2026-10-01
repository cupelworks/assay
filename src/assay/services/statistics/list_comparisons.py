"""List comparisons, newest first, by batch or by scope."""
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import StatisticalComparisonModel
from assay.schemas.statistics import ComparisonList
from assay.services.statistics._comparisons import describe_many


async def list_comparisons(session: AsyncSession, *, offset: int, limit: int,
                           batch_id: uuid.UUID | None = None, test_id: uuid.UUID | None = None,
                           test_set_id: uuid.UUID | None = None,
                           test_plan_id: uuid.UUID | None = None) -> ComparisonList:
    model = StatisticalComparisonModel
    filters = []
    if batch_id is not None:
        filters.append(or_(model.batch_a_id == batch_id, model.batch_b_id == batch_id))
    for column, value in ((model.test_id, test_id), (model.test_set_id, test_set_id),
                          (model.test_plan_id, test_plan_id)):
        if value is not None:
            filters.append(column == value)
    total = await session.scalar(select(func.count()).select_from(model).where(*filters))
    page = (await session.scalars(
        select(model).where(*filters).order_by(model.created_at.desc(), model.id)
        .offset(offset).limit(limit))).all()
    items = await describe_many(list(page), session, with_result=False)
    return ComparisonList(items=items, total=total, offset=offset, limit=limit)
