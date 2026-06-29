import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
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
