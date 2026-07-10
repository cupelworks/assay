from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestPlanModel
from assay.schemas import TestPlanName


async def _check_unique_test_plan_name_or_409(
        request: TestPlanName,
        session: AsyncSession,
) -> None:
    """Raise 409 if a test plan with the given name already exists.

    Args:
        request: Request containing the test plan name to check.
        session: Active async database session.

    Raises:
        HTTPException: 409 if the name is already taken.
    """
    found = await session.scalar(
        select(TestPlanModel.name)
        .where(TestPlanModel.name == request.name)
    )

    if found is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Test plan with name '{request.name}' already exists"
        )
