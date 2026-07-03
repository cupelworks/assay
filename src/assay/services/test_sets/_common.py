import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import select

from assay.models import TestSetModel
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
