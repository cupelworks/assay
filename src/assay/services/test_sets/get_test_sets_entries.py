import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestSetEntryModel
from assay.schemas import PaginatedTestSetEntriesDetails, TestSetEntryDetails
from assay.services.test_sets._common import (
    _describe_entries,
    _find_test_set_entry_in_specific_test_set_or_404,
    _find_test_set_or_404,
)


async def get_test_sets_linked_tests(
        test_set_id: uuid.UUID,
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedTestSetEntriesDetails:
    """Orchestrates test set entry listing: validates the set exists, counts total
    entries, fetches the requested page, and returns a paginated response.

    Args:
        test_set_id: The UUID of the test set whose entries are being listed.
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with test set entry details, total count, offset, and limit.

    Raises:
        HTTPException 404: No test set exists with the given ID.
    """
    await _find_test_set_or_404(test_set_id, session)

    total = await session.scalar(select(func.count(TestSetEntryModel.test_id))
                                 .where(TestSetEntryModel.test_set_id == test_set_id)) or 0

    # `name` is not unique, so it alone can't guarantee a stable row order across
    # pages. `id` is the primary key and therefore always unique, so appending it
    # as a tiebreaker makes the ordering — and the pagination — fully deterministic.
    found = (
        await session.scalars(
            select(TestSetEntryModel)
            .where(TestSetEntryModel.test_set_id == test_set_id)
            .order_by(TestSetEntryModel.name, TestSetEntryModel.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()

    return PaginatedTestSetEntriesDetails(
        offset=offset,
        limit=limit,
        total=total,
        items=await _describe_entries(list(found), session),
    )


async def get_test_set_linked_test_by_entry_id(
        test_set_id: uuid.UUID,
        entry_id: uuid.UUID,
        session: AsyncSession,
) -> TestSetEntryDetails:
    """Fetch a single entry from a test set by its ID.

    Args:
        test_set_id: UUID of the test set the entry must belong to.
        entry_id: UUID of the entry to retrieve.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The entry's details, including a trace back to the live test it was snapshotted from.

    Raises:
        HTTPException 404: The test set does not exist, or no entry with that ID exists in it.
    """
    await _find_test_set_or_404(test_set_id, session)
    found = await _find_test_set_entry_in_specific_test_set_or_404(test_set_id, entry_id, session)

    (described,) = await _describe_entries([found], session)
    return described
