import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestRunModel, TestSetExecutionModel
from assay.schemas import (
    PaginatedStandaloneRunCreationMetadata,
    PaginatedTestSetRunCreationMetadata,
    StandaloneRunCreationMetadata,
    TestCaseID,
    TestSetID,
    TestSetReplayedExecutionID,
    TestSetRunCreationMetadata,
)
from assay.services.test_sets._common import _find_test_set_or_404
from assay.services.tests._common import _find_test_by_id_or_404


async def get_standalone_run_metadata_all_test_runs(
        test_id: uuid.UUID,
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedStandaloneRunCreationMetadata:
    """Orchestrates standalone run listing for a test: validates the test ID, counts
    total runs, fetches the requested page, and returns a paginated response.

    Args:
        test_id: The UUID of the test whose runs are being listed.
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with each run's ID, status, created_at, and test_case_id,
        plus total count, offset, and limit.

    Raises:
        HTTPException 404: No test exists with the given ID.
    """
    await _find_test_by_id_or_404(test_id, session)
    
    total = await session.scalar(
        select(func.count(TestRunModel.id)).where(TestRunModel.test_id == test_id)
    ) or 0

    found = (await session.execute(
        select(TestRunModel.id, TestRunModel.status, TestRunModel.created_at)
        .where(TestRunModel.test_id == test_id)
        .order_by(TestRunModel.created_at.desc(), TestRunModel.id.desc())
        .offset(offset)
        .limit(limit)
    )).all()
    
    return PaginatedStandaloneRunCreationMetadata(
        total=total,
        offset=offset,
        limit=limit,
        items=[
            StandaloneRunCreationMetadata(
                id=item.id,
                status=item.status,
                created_at=item.created_at,
                test_case_id=TestCaseID(id=test_id),
            )
            for item in found
        ],
    )


async def get_test_set_run_metadata_all_test_runs(
        test_set_id: uuid.UUID,
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedTestSetRunCreationMetadata:
    await _find_test_set_or_404(test_set_id, session)
    
    total = await session.scalar(
        select(func.count(TestSetExecutionModel.id))
        .where(TestSetExecutionModel.test_set_id == test_set_id)
    ) or 0

    found = (await session.execute(
        select(
            TestSetExecutionModel.id,
            TestSetExecutionModel.created_at,
            TestSetExecutionModel.replayed_execution_id,
            func.count(TestRunModel.id).label("run_count"),
        )
        .join(
            TestRunModel,
            TestSetExecutionModel.id == TestRunModel.test_set_execution_id,
            isouter=True,
        )
        .where(TestSetExecutionModel.test_set_id == test_set_id)
        .group_by(TestSetExecutionModel.id)
        .order_by(TestSetExecutionModel.created_at.desc(), TestSetExecutionModel.id.desc())
        .offset(offset)
        .limit(limit)
    )).all()
    
    return PaginatedTestSetRunCreationMetadata(
        total=total,
        offset=offset,
        limit=limit,
        items=[
            TestSetRunCreationMetadata(
                id=item.id,
                created_at=item.created_at,
                test_set_id=TestSetID(id=test_set_id),
                run_count=item.run_count,
                replayed_execution_id=TestSetReplayedExecutionID(id=item.replayed_execution_id)
                                      if item.replayed_execution_id else None,
            )
            for item in found
        ]
    )
