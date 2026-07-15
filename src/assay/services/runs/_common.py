import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestSetEntryModel


async def _find_test_set_entries_ids_or_409(
        test_set_id: uuid.UUID,
        session: AsyncSession,
) -> list[uuid.UUID]:
    """Fetch test set's entry IDs, raising 409 if it has none.

    A test set existing is not enough to make it a valid live-execution
    target — an empty test set would fan out into zero runs, which looks
    identical to the caller as a successful no-op. Rejecting it upfront
    surfaces the mistake immediately, the same reasoning already applied to
    empty test plan scope and to standalone runs against a test with no
    test types assigned. Does not check whether the test set itself exists
    — callers are expected to run that check first.

    Returns the entry IDs directly (not just a count) so the caller can
    build one TestRunModel per entry without a second query.

    Args:
        test_set_id: UUID of the test set to fetch entry IDs for.
        session: Active async database session.

    Returns:
        The IDs of every entry currently in the test set (always >= 1 item).

    Raises:
        HTTPException: 409 if the test set has zero entries.
    """
    entries_ids = list((await session.scalars(
        select(TestSetEntryModel.id)
        .where(TestSetEntryModel.test_set_id == test_set_id)
    )).all())
    
    if not entries_ids:
        raise HTTPException(
            status_code=409,
            detail=f"No Test Set Entries found in Test set with ID '{test_set_id}'"
        )

    return entries_ids
