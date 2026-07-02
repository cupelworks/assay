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
