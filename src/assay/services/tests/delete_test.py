from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from assay.models import TestModel, TestRunModel, TestSetEntryModel
from assay.schemas import TestCaseID
from assay.services.tests._common import _find_all_tests_or_404


async def _assert_not_referenced(
        session: AsyncSession,
        column,
        ids: list,
        reason: str) -> None:
    """Raise 409 if any of the given test IDs are referenced by another table.

    Args:
        session: Active async database session.
        column: The FK column to check (e.g. TestSetEntryModel.test_id).
        ids: List of test UUIDs to check.
        reason: Human-readable label for the referencing entity (e.g. "test sets").

    Raises:
        HTTPException: 409 if any ID is found in the referencing table.
    """
    # select only the FK column — no need to load full model instances
    found = list((await session.scalars(
        select(column)
        .where(column.in_(ids))
        .distinct()  # a test may be referenced multiple times; report each ID once
    )).all())

    if found:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tests with ids {[str(t) for t in found]} cannot be deleted "
               f"because they are linked to {reason}"
        )


async def delete_test_by_id(
        request: list[TestCaseID],
        session: AsyncSession) -> None:
    """Delete a list of test cases by ID.

    Validates existence, checks referential integrity, then performs a single bulk DELETE.

    Args:
        request: List of test case IDs to delete.
        session: Active async database session.

    Raises:
        HTTPException: 404 if any of the requested IDs do not exist in the database.
        HTTPException: 409 if any test is linked to a test set or test run.
            The test must be unlinked from the set, or past run records must be deleted first.
    """
    ids = [r.id for r in request]

    # raise 404 early if any ID is unknown — no point checking references for missing tests
    await _find_all_tests_or_404(ids, session)

    # raise 409 if any test is still referenced — deleting would break referential integrity
    await _assert_not_referenced(
        session, TestSetEntryModel.test_id, ids, "test sets"
    )
    await _assert_not_referenced(session, TestRunModel.test_id, ids, "test runs")

    # single bulk statement instead of one DELETE per row
    await session.execute(
        delete(TestModel).where(TestModel.id.in_(ids))
    )

    await session.commit()
