import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.services.test_sets._common import (
    _check_test_set_entries_have_no_runs_or_409,
    _find_test_set_or_404,
)


async def delete_test_set_by_id(
        test_set_id: uuid.UUID,
        session: AsyncSession,
) -> None:
    """Delete a test set and all of its entries.

    Runs two guards before deleting: the test set must exist (404), and none
    of its entries may have runs (409) — deleting a set whose entries have
    been executed would take the runs' frozen record of what they ran against
    down with it. Once both guards pass, deleting the set cascades to its
    entries at the database level (ON DELETE CASCADE).

    The 409 guard is the app-level check that produces the clean error. The
    entry -> run foreign key deliberately has no cascade, so even if a run
    were created between the check and the commit, the database would refuse
    the cascade rather than silently destroy run history.

    Args:
        test_set_id: UUID of the test set to delete.
        session: Active async database session.

    Raises:
        HTTPException: 404 if no test set with the given ID exists.
        HTTPException: 409 if any of the test set's entries have runs.
    """
    found = await _find_test_set_or_404(test_set_id, session)
    await _check_test_set_entries_have_no_runs_or_409(test_set_id, session)

    await session.delete(found)
    await session.commit()
