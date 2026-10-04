import logging

from fastapi import HTTPException
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from assay.models import TestModel, TestSetEntryModel
from assay.schemas import TestCaseID
from assay.services._standing import keys_with, tests_with_runs
from assay.services.tests._common import _find_all_tests_or_404

logger = logging.getLogger(__name__)


def _raise_if_referenced(ids: list, referenced: set, reason: str) -> None:
    """Raise 409 if any of the given test ids is referenced, naming each once,
    in the order they were asked for.

    Args:
        ids: The test UUIDs asked to be deleted.
        referenced: Those among them referenced elsewhere.
        reason: Human-readable label for the referencing entity (e.g. "test sets").

    Raises:
        HTTPException: 409 if any id is referenced.
    """
    found = [test_id for test_id in ids if test_id in referenced]
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
    _raise_if_referenced(ids, await keys_with(session, TestSetEntryModel.test_id, ids),
                         "test sets")
    _raise_if_referenced(ids, await tests_with_runs(session, ids), "test runs")

    # single bulk statement instead of one DELETE per row
    await session.execute(
        delete(TestModel).where(TestModel.id.in_(ids))
    )

    await session.commit()

    logger.info("Deleted %d tests", len(ids), extra={"test_count": len(ids)})
