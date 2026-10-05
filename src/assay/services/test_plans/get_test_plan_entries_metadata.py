# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from assay.models import TestPlanEntryModel, TestSetModel
from assay.schemas import PaginatedTestPlanEntriesDetails, TestPlanEntryDetails
from assay.services.test_plans._common import _find_test_plan_by_id_or_404
from assay.services.test_sets._common import _describe_test_sets


async def get_all_test_plan_entries_metadata(
        test_plan_id: uuid.UUID,
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedTestPlanEntriesDetails:
    """Orchestrates test plan entry listing: validates the plan, counts its entries,
    fetches the requested page, and returns a paginated response.

    Each entry links the test plan to one of its test sets — the response embeds
    that test set's metadata (`id`, `name`, `created_at`) alongside the entry's own
    ID. Results are ordered by the linked test set's `name`, with its `id` as a
    tiebreaker, so pagination is stable across pages even when multiple test sets
    share the same name.

    Args:
        test_plan_id: UUID of the test plan whose entries should be listed.
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with test plan entry details, total count, offset,
        and limit.

    Raises:
        HTTPException: 404 if no test plan exists with the given ID.
    """
    await _find_test_plan_by_id_or_404(test_plan_id, session)

    total = await session.scalar(select(func.count(TestPlanEntryModel.id))
                                 .where(TestPlanEntryModel.test_plan_id == test_plan_id)) or 0

    found_entries = (await session.scalars(
        select(TestPlanEntryModel)
        .join(TestPlanEntryModel.test_set)
        .where(TestPlanEntryModel.test_plan_id == test_plan_id)
        .options(contains_eager(TestPlanEntryModel.test_set))
        .order_by(TestSetModel.name.desc(), TestSetModel.id.desc())
        .offset(offset)
        .limit(limit)
    )).all()

    test_sets = await _describe_test_sets([entry.test_set for entry in found_entries],
                                          session)

    return PaginatedTestPlanEntriesDetails(
        total=total, offset=offset, limit=limit,
        items=[TestPlanEntryDetails(id=entry.id, test_set=test_set)
               for entry, test_set in zip(found_entries, test_sets, strict=True)])
