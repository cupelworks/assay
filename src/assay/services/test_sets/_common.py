import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from assay.models import TestRunModel, TestSetEntryModel, TestSetModel
from assay.schemas import TestSetEntryID, TestSetName


async def _check_unique_test_set_name_or_409(
        request: TestSetName,
        session: AsyncSession
) -> None:
    """Raise 409 if a test set with the given name already exists.

    Args:
        request: Request containing the test set name to check.
        session: Active async database session.

    Raises:
        HTTPException: 409 if the name is already taken.
    """
    existing = await session.scalar(
        select(TestSetModel.name)
        .where(TestSetModel.name == request.name)
    )

    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Test set with name '{request.name}' already exists"
        )


async def _find_test_set_or_404(test_set_id: uuid.UUID, session: AsyncSession):
    """Fetch a test set by ID, raising 404 if it does not exist.

    Args:
        test_set_id: The UUID of the test set to look up.
        session: Active async database session.

    Returns:
        The matching TestSetModel instance.

    Raises:
        HTTPException: 404 if no test set with the given ID exists.
    """
    found = await session.scalar(select(TestSetModel).where(TestSetModel.id == test_set_id))

    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"Test set with ID '{test_set_id}' not found"
        )

    return found


async def _find_test_sets_or_404(
        test_set_ids: list[uuid.UUID],
        session: AsyncSession,
) -> list[uuid.UUID]:
    """Fetch the given test set IDs, raising 404 if any of them does not exist.

    The query's `IN (...)` clause naturally deduplicates: each existing ID is
    returned exactly once no matter how many times it appears in `test_set_ids`,
    so callers can use the returned list to build downstream records without a
    separate deduplication step.

    Args:
        test_set_ids: The test set IDs to look up.
        session: Active async database session.

    Returns:
        The subset of `test_set_ids` that exist, with duplicates collapsed.

    Raises:
        HTTPException: 404 listing the IDs that don't exist.
    """
    found_ids = (await session.scalars(
        select(TestSetModel.id)
        .where(TestSetModel.id.in_(test_set_ids))
    )).all()

    difference = set(test_set_ids) - set(found_ids)

    if difference:
        raise HTTPException(
            status_code=404,
            detail=f"Test sets with IDs {[str(_id) for _id in difference]} not found"
        )

    return list(found_ids)


async def _check_tests_not_in_test_set_or_409(
        test_set_id: uuid.UUID,
        test_ids: list[uuid.UUID],
        session: AsyncSession,
):
    """Raise 409 if any of the given tests are already snapshotted in the test set.

    Queries existing entries by (test_set_id, test_id) and raises immediately if
    any overlap is found. The detail message lists the conflicting IDs so the caller
    knows exactly which tests to remove from the request.

    Args:
        test_set_id: UUID of the test set to check against.
        test_ids: List of test UUIDs the caller intends to add.
        session: Active async database session.

    Raises:
        HTTPException: 409 listing the test IDs already present in the test set.
    """
    found = (await session.scalars(
        select(TestSetEntryModel.test_id)
        .where(TestSetEntryModel.test_set_id == test_set_id)
        .where(TestSetEntryModel.test_id.in_(test_ids))
    )).all()

    if found:
        raise HTTPException(
            status_code=409,
            detail=f"Tests with ID '{[str(_id) for _id in found]}' "
                   f"already linked to test set with ID '{test_set_id}'"
        )


async def _find_test_set_entry_in_specific_test_set_or_404(
        test_set_id: uuid.UUID,
        entry_id: uuid.UUID,
        session: AsyncSession
):
    """Fetch a test set entry by ID, scoped to its parent test set, raising 404 if not found.

    Args:
        test_set_id: UUID of the test set the entry must belong to.
        entry_id: UUID of the entry to look up.
        session: Active async database session.

    Returns:
        The matching TestSetEntryModel instance.

    Raises:
        HTTPException: 404 if no entry with the given ID exists in that test set.
    """
    found = await session.scalar(
        select(TestSetEntryModel)
        .where(TestSetEntryModel.test_set_id == test_set_id)
        .where(TestSetEntryModel.id == entry_id))

    if found is None:
        raise HTTPException(
            status_code=404,
            detail=f"Test entry with ID '{entry_id}' not found "
                   f"in test set with ID '{test_set_id}'"
        )

    return found


async def _find_test_set_entries_in_specific_test_set_or_404(
        test_set_id: uuid.UUID,
        entries: list[TestSetEntryID],
        session: AsyncSession,
):
    """Fetch multiple test set entries by ID, scoped to their parent test set.

    Raises 404 if any requested ID does not resolve to an entry in this test
    set — either because no entry with that ID exists at all, or because it
    belongs to a different test set. Duplicate IDs in the request are
    silently deduplicated.

    Args:
        test_set_id: UUID of the test set the entries must belong to.
        entries: IDs of the entries to look up.
        session: Active async database session.

    Returns:
        The matching TestSetEntryModel instances.

    Raises:
        HTTPException: 404 if one or more entry IDs don't resolve to an
            entry in this test set.
    """
    entries_ids = [entry.id for entry in entries]

    finding = (await session.scalars(
        select(TestSetEntryModel)
        .where(TestSetEntryModel.test_set_id == test_set_id)
        .where(TestSetEntryModel.id.in_(entries_ids))
    )).all()

    found_ids = [found.id for found in finding]

    difference = set(entries_ids) - set(found_ids)

    if difference:
        raise HTTPException(
            status_code=404,
            detail=f"Test entries with ID '{[str(_id) for _id in difference]}' "
                   f"not linked to test set with ID '{test_set_id}'"
        )

    return finding


async def _check_test_set_entry_has_no_runs_or_409(
        entry_id: uuid.UUID,
        session: AsyncSession
) -> None:
    """Raise 409 if the entry has already been executed at least once.

    Once a run references a test set entry, the entry must stay frozen so the
    run's record of what it executed against remains accurate.

    Args:
        entry_id: UUID of the test set entry to check.
        session: Active async database session.

    Raises:
        HTTPException: 409 if a TestRunModel already references this entry.
    """
    has_runs = await session.scalar(
        select(TestRunModel.id)
        .where(TestRunModel.test_set_entry_id == entry_id)
        .limit(1)
    )

    if has_runs is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Test entry with ID '{entry_id}' can't be modified"
                   f" because it has runs"
        )


async def _check_test_set_entries_have_no_runs_or_409(
        test_set_id: uuid.UUID,
        session: AsyncSession,
) -> None:
    """Raise 409 if any entry in the test set has already been executed.

    A test set can't be deleted while one of its entries has runs, since
    deleting it would take the run's frozen record of what it executed
    against down with it.

    Args:
        test_set_id: UUID of the test set to check.
        session: Active async database session.

    Raises:
        HTTPException: 409 if a TestRunModel references any entry belonging
            to this test set.
    """
    has_runs = await session.scalar(
        select(TestRunModel.id)
        .join(TestSetEntryModel, TestRunModel.test_set_entry_id == TestSetEntryModel.id)
        .where(TestSetEntryModel.test_set_id == test_set_id)
        .limit(1)
    )

    if has_runs is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Test set with ID '{test_set_id}' can't be deleted "
                   f"because one or more of its entries have runs"
        )


async def _check_given_test_set_entries_have_no_runs_or_409(
        entries: list[TestSetEntryID],
        session: AsyncSession,
) -> None:
    """Raise 409 if any of the given entries has already been executed.

    Scoped only to the entries passed in, not the whole test set they
    belong to — a run on some other entry in the same set is not a reason
    to block these ones. The detail message lists the IDs of the entries
    that have runs, so the caller knows exactly which ones to remove from
    the request.

    Args:
        entries: The test set entries to check.
        session: Active async database session.

    Raises:
        HTTPException: 409 listing the entry IDs that already have runs.
    """
    ids_only = [entry.id for entry in entries]

    has_runs = (await session.scalars(
        select(TestRunModel.test_set_entry_id)
        .where(TestRunModel.test_set_entry_id.in_(ids_only))
    )).all()

    if has_runs:
        raise HTTPException(
            status_code=409,
            detail=f"The Test Set Entries with ID "
                   f"{[str(_id) for _id in has_runs]} have runs, "
                   f"therefore they can't be deleted"
        )
