import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from assay.models import TestRunModel, TestSetEntryModel, TestSetModel
from assay.schemas import TestSetName


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
            detail=f"Test entry with ID '{entry_id}' can't be updated"
                   f" because it has runs"
        )
