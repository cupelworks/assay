import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestSetEntryModel
from assay.schemas import TestSetEntryID
from assay.services.test_sets._common import (
    _check_given_test_set_entries_have_no_runs_or_409,
    _check_test_set_entries_have_no_runs_or_409,
    _find_test_set_entries_in_specific_test_set_or_404,
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


async def delete_test_set_entries_by_id(
        test_set_id: uuid.UUID,
        request: list[TestSetEntryID],
        session: AsyncSession,
) -> None:
    """Delete one or more entries from a test set in a single bulk operation.

    Runs three guards before deleting: the test set must exist (404), every
    requested entry must exist within that test set (404, listing any that
    don't), and none of them may have runs (409, listing any that do) —
    deleting an entry that has been executed would take the runs' frozen
    record of what they ran against down with it.

    The 409 guard is the app-level check that produces the clean error. The
    actual delete is a single bulk statement rather than one delete per
    entry, so it bypasses ORM-level cascade handling — the backstop against
    a run sneaking in between the check and the commit is the plain,
    non-cascading foreign key from test_runs to test_set_entries, which the
    database enforces regardless of how the delete was issued.

    Args:
        test_set_id: UUID of the test set the entries belong to.
        request: IDs of the entries to delete.
        session: Active async database session.

    Raises:
        HTTPException: 404 if the test set does not exist, or one or more
            requested entries don't exist in it.
        HTTPException: 409 if one or more requested entries have runs.
    """
    await _find_test_set_or_404(test_set_id, session)
    found = await _find_test_set_entries_in_specific_test_set_or_404(test_set_id, request, session)
    await _check_given_test_set_entries_have_no_runs_or_409(request, session)

    await session.execute(
        delete(TestSetEntryModel).where(
            TestSetEntryModel.id.in_([entry.id for entry in found])
        )
    )
    await session.commit()
