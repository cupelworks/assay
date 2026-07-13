import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestPlanModel
from assay.schemas import PaginatedTestPlanMetadataResponse, TestPlanMetadata
from assay.services.test_plans._common import _find_test_plan_by_id_or_404


async def get_all_test_plans_metadata(
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedTestPlanMetadataResponse:
    """Orchestrates test plan listing: counts total records, fetches the requested page,
    and returns a paginated response.

    Args:
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with test plan metadata, total count, offset, and limit.
    """
    total = await session.scalar(select(func.count(TestPlanModel.id))) or 0
    
    found = (await session.scalars(
        select(TestPlanModel)
        .offset(offset)
        .limit(limit)
        .order_by(TestPlanModel.created_at.desc(), TestPlanModel.id)
    )).all()
    
    return PaginatedTestPlanMetadataResponse(
        total=total,
        offset=offset,
        limit=limit,
        items=[
            TestPlanMetadata(
                id=test_plan.id,
                name=test_plan.name,
                created_at=test_plan.created_at,
            )
            for test_plan in found
        ]
    )


async def get_test_plan_metadata_by_id(
        test_plan_id: uuid.UUID,
        session: AsyncSession,
) -> TestPlanMetadata:
    """Orchestrates single test plan retrieval: validates ID and returns its metadata.

    Args:
        test_plan_id: The UUID of the test plan to retrieve.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The test plan metadata (id, name, created_at).

    Raises:
        HTTPException 404: No test plan exists with the given ID.
    """
    test_plan_model = await _find_test_plan_by_id_or_404(test_plan_id, session)

    return TestPlanMetadata(
        id=test_plan_model.id,
        name=test_plan_model.name,
        created_at=test_plan_model.created_at,
    )
