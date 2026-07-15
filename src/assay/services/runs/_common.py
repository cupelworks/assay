import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestRunModel, TestSetEntryModel
from assay.models.test import TestSetExecutionModel


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


async def _check_test_set_execution_or_404(
        test_set_execution_id: uuid.UUID,
        session: AsyncSession,
):
    """Raise 404 if no test set execution with the given ID exists.

    Unscoped — does not check which test set the execution belongs to. Use
    _check_test_set_execution_id_linked_to_specific_test_set_id_or_404 when
    the execution also needs to be scoped to a specific test set, so the two
    failure modes ("doesn't exist at all" vs. "exists, but not for this
    test set") get distinct, precise messages instead of being collapsed
    into one.

    Args:
        test_set_execution_id: UUID of the test set execution to check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if no test set execution with this ID exists.
    """
    found = await session.scalar(
        select(TestSetExecutionModel.id)
        .where(TestSetExecutionModel.id == test_set_execution_id)
    )

    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"Test set execution with ID '{test_set_execution_id}' does not exist"
        )


async def _check_test_set_execution_id_linked_to_specific_test_set_id_or_404(
        test_set_id: uuid.UUID,
        test_set_execution_id: uuid.UUID,
        session: AsyncSession,
):
    """Raise 404 if the given execution doesn't belong to the given test set.

    Scoped lookup, same shape as _find_test_plan_entries_or_404: prevents a
    caller from replaying execution X of test set A by hitting test set B's
    endpoint. Callers are expected to have already confirmed the execution
    exists at all (via _check_test_set_execution_or_404) — this only
    distinguishes "exists, but belongs to a different test set" from that.

    Args:
        test_set_id: UUID of the test set the execution must belong to.
        test_set_execution_id: UUID of the test set execution to check.
        session: Active async database session.

    Raises:
        HTTPException: 404 if the execution isn't linked to this test set.
    """
    found = await session.scalar(
        select(TestSetExecutionModel.id)
        .where(TestSetExecutionModel.id == test_set_execution_id)
        .where(TestSetExecutionModel.test_set_id == test_set_id)
    )

    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"Test set execution with ID '{test_set_execution_id}' not linked "
                   f"to test set with ID '{test_set_id}'"
        )


async def _find_test_set_execution_id_entries_or_409(
        test_set_execution_id: uuid.UUID,
        session: AsyncSession,
):
    """Fetch a test set execution's run entry IDs, raising 409 if it has none.

    An entry can be unlinked from its set (test_set_id set to NULL) after
    being run without invalidating the run itself, so reading through
    test_set_execution_id here keeps returning every entry that execution
    actually ran, regardless of current set membership. Callers needing
    "does this execution belong to this test set" should use
    _check_test_set_execution_id_linked_to_specific_test_set_id_or_404 first
    — this function only reads the execution's already-frozen run history.

    Same empty-target reasoning as _find_test_set_entries_ids_or_409: a
    replay execution with zero runs to reproduce would silently succeed
    with an empty scope, indistinguishable to the caller from a real
    replay.

    Args:
        test_set_execution_id: UUID of the test set execution to fetch entry IDs for.
        session: Active async database session.

    Returns:
        The test_set_entry_id of every run belonging to this execution (always >= 1 item).

    Raises:
        HTTPException: 409 if the execution has zero runs.
    """
    found = list((await session.scalars(
        select(TestRunModel.test_set_entry_id)
        .where(TestRunModel.test_set_execution_id == test_set_execution_id)
    )).all())

    if not found:
        raise HTTPException(
            status_code=409,
            detail=f"Test set execution with ID '{test_set_execution_id}' has no test set entries"
        )

    return found
