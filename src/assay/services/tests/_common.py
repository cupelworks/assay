import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette import status

from assay.models import TestModel


async def _find_all_tests_or_404(
        test_ids: list[uuid.UUID],
        session: AsyncSession) -> None:
    """Raise 404 if any of the given test IDs do not exist in the database.

    Args:
        test_ids: List of test UUIDs to look up.
        session: Active async database session.

    Raises:
        HTTPException: 404 listing the IDs that were not found.
    """
    # select only the id column — no need to load full model instances
    found_test_ids = list((await session.scalars(
        select(TestModel.id)
        .where(TestModel.id.in_(test_ids))
    )).all())

    # set difference identifies which requested IDs are missing from the DB
    difference = set(test_ids) - set(found_test_ids)

    if difference:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tests with ids {[str(test_id) for test_id in difference]} not found",
        )


async def _find_test_by_id_or_404(
        test_id: uuid.UUID,
        session: AsyncSession
):
    """Fetch a single test case by ID, eagerly loading its type assignments, or raise 404.

    Args:
        test_id: UUID of the test case to fetch.
        session: Active async database session.

    Returns:
        The matching TestModel with test_type_assignments already loaded.

    Raises:
        HTTPException: 404 if no test with the given ID exists.
    """
    found = await session.scalar(
        select(TestModel).where(TestModel.id == test_id)
        .options(selectinload(TestModel.test_type_assignments))
    )
    
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Test with id {str(test_id)} not found",
        )

    return found
