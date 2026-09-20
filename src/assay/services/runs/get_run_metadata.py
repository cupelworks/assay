import uuid

from sqlalchemy import func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestPlanExecutionModel, TestRunModel, TestSetExecutionModel
from assay.schemas import (
    PaginatedRunMetadata,
    PaginatedStandaloneRunCreationMetadata,
    PaginatedTestPlanExecutionMetadata,
    PaginatedTestPlanExecutionRunMetadata,
    PaginatedTestSetExecutionMetadata,
    PaginatedTestSetExecutionRunMetadata,
    RunMetadata,
    RunOrigin,
    StandaloneRunCreationMetadata,
    TestCaseID,
    TestPlanExecutionMetadata,
    TestPlanExecutionRunMetadata,
    TestPlanID,
    TestPlanReplayedExecutionID,
    TestSetEntryID,
    TestSetExecutionID,
    TestSetExecutionMetadata,
    TestSetExecutionRunMetadata,
    TestSetID,
    TestSetReplayedExecutionID,
)
from assay.schemas.runs import TestPlanExecutionID
from assay.services.runs._common import (
    _check_test_plan_execution_id_linked_to_specific_test_plan_id_or_404,
    _check_test_plan_execution_or_404,
    _check_test_set_execution_id_linked_to_specific_test_set_id_or_404,
    _check_test_set_execution_or_404,
)
from assay.services.test_plans._common import _find_test_plan_by_id_or_404
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


async def get_run_metadata_all_runs(
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedRunMetadata:
    """Orchestrates run listing across the entire system, regardless of
    origin: counts every TestRunModel row that exists, fetches the requested
    page, and returns a paginated response with each row's origin resolved
    from TestRunModel's own mode invariant.

    Unlike every other listing in this module, this has no parent resource
    to validate — no guard runs first — and no origin-specific filter: every
    run is in scope, whether it was created standalone, via a test set
    execution, or via a test plan execution. A single unfiltered query over
    TestRunModel covers all three, since the mode a row belongs to is
    already fully determined by which of its own FK columns is set (see
    TestRunModel's docstring) — no join needed to tell them apart.

    Args:
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with each run's ID, status, created_at,
        origin, and the origin-specific ID(s) that follow from it (exactly
        one of `test_case_id`, or the `test_set_entry_id` +
        `test_set_execution_id`/`test_plan_execution_id` pair, is non-null
        per item), plus total count, offset, and limit.
    """
    total = await session.scalar(select(func.count(TestRunModel.id))) or 0

    found = (await session.execute(
        select(
            TestRunModel.id,
            TestRunModel.status,
            TestRunModel.created_at,
            TestRunModel.test_id,
            TestRunModel.test_set_entry_id,
            TestRunModel.test_set_execution_id,
            TestRunModel.test_plan_execution_id,
        )
        .order_by(TestRunModel.created_at.desc(), TestRunModel.id.desc())
        .offset(offset)
        .limit(limit)
    )).all()

    return PaginatedRunMetadata(
        total=total,
        offset=offset,
        limit=limit,
        items=[_run_metadata_from_row(item) for item in found],
    )


def _run_metadata_from_row(row: Row) -> RunMetadata:
    """Resolve a single TestRunModel row's origin from its own FK columns.

    Mirrors TestRunModel's documented mode invariant exactly: `test_id` set
    means standalone, `test_set_execution_id` set means test-set-triggered,
    `test_plan_execution_id` set means test-plan-triggered — the three
    patterns are mutually exclusive by construction, so checking them in
    this order is enough to classify every row.

    Args:
        row: A result row carrying id, status, created_at, test_id,
            test_set_entry_id, test_set_execution_id, and
            test_plan_execution_id.

    Returns:
        The row's RunMetadata, with `origin` and only the ID field(s) that
        origin implies populated — the rest left null.
    """
    if row.test_id is not None:
        return RunMetadata(
            id=row.id,
            status=row.status,
            created_at=row.created_at,
            origin=RunOrigin.standalone,
            test_case_id=TestCaseID(id=row.test_id),
            test_set_entry_id=None,
            test_set_execution_id=None,
            test_plan_execution_id=None,
        )

    if row.test_set_execution_id is not None:
        return RunMetadata(
            id=row.id,
            status=row.status,
            created_at=row.created_at,
            origin=RunOrigin.test_set,
            test_case_id=None,
            test_set_entry_id=TestSetEntryID(id=row.test_set_entry_id),
            test_set_execution_id=TestSetExecutionID(id=row.test_set_execution_id),
            test_plan_execution_id=None,
        )

    return RunMetadata(
        id=row.id,
        status=row.status,
        created_at=row.created_at,
        origin=RunOrigin.test_plan,
        test_case_id=None,
        test_set_entry_id=TestSetEntryID(id=row.test_set_entry_id),
        test_set_execution_id=None,
        test_plan_execution_id=TestPlanExecutionID(id=row.test_plan_execution_id),
    )


async def get_test_set_execution_metadata_all_executions(
        test_set_id: uuid.UUID,
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedTestSetExecutionMetadata:
    """Orchestrates test set execution listing: validates the test set ID, counts
    total executions, fetches the requested page (each row's run_count computed
    via an outer join against TestRunModel grouped by execution ID, so an
    execution with zero runs still shows up with run_count=0 instead of being
    dropped), and returns a paginated response.

    Args:
        test_set_id: The UUID of the test set whose executions are being listed.
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with each execution's ID, created_at, test_set_id,
        run_count, and replayed_execution_id, plus total count, offset, and limit.

    Raises:
        HTTPException 404: No test set exists with the given ID.
    """
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
    
    return PaginatedTestSetExecutionMetadata(
        total=total,
        offset=offset,
        limit=limit,
        items=[
            TestSetExecutionMetadata(
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


async def get_test_plan_execution_metadata_all_executions(
        test_plan_id: uuid.UUID,
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedTestPlanExecutionMetadata:
    """Orchestrates test plan execution listing: validates the test plan ID, counts
    total executions, fetches the requested page (each row's run_count computed
    via an outer join against TestRunModel grouped by execution ID, so an
    execution with zero runs still shows up with run_count=0 instead of being
    dropped), and returns a paginated response.

    Args:
        test_plan_id: The UUID of the test plan whose executions are being listed.
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with each execution's ID, created_at, test_plan_id,
        run_count, and replayed_execution_id, plus total count, offset, and limit.

    Raises:
        HTTPException 404: No test plan exists with the given ID.
    """
    await _find_test_plan_by_id_or_404(test_plan_id, session)

    total = await session.scalar(
        select(func.count(TestPlanExecutionModel.id))
        .where(TestPlanExecutionModel.test_plan_id == test_plan_id)
    ) or 0

    found = (await session.execute(
        select(
            TestPlanExecutionModel.id,
            TestPlanExecutionModel.created_at,
            TestPlanExecutionModel.replayed_execution_id,
            func.count(TestRunModel.id).label("run_count"),
        )
        .join(
            TestRunModel,
            TestPlanExecutionModel.id == TestRunModel.test_plan_execution_id,
            isouter=True,
        )
        .where(TestPlanExecutionModel.test_plan_id == test_plan_id)
        .group_by(TestPlanExecutionModel.id)
        .order_by(TestPlanExecutionModel.created_at.desc(), TestPlanExecutionModel.id.desc())
        .offset(offset)
        .limit(limit)
    )).all()

    return PaginatedTestPlanExecutionMetadata(
        total=total,
        offset=offset,
        limit=limit,
        items=[
            TestPlanExecutionMetadata(
                id=item.id,
                created_at=item.created_at,
                test_plan_id=TestPlanID(id=test_plan_id),
                run_count=item.run_count,
                replayed_execution_id=TestPlanReplayedExecutionID(id=item.replayed_execution_id)
                                      if item.replayed_execution_id else None,
            )
            for item in found
        ]
    )


async def get_test_set_execution_run_metadata_all_runs(
        test_set_id: uuid.UUID,
        test_set_execution_id: uuid.UUID,
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedTestSetExecutionRunMetadata:
    """Orchestrates run listing for a single test set execution: validates the
    test set ID, the execution ID, and that the execution belongs to this
    test set, counts total runs the execution produced, fetches the
    requested page, and returns a paginated response.

    Args:
        test_set_id: The UUID of the test set the execution must belong to.
        test_set_execution_id: The UUID of the execution whose runs are being listed.
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with each run's ID, status, created_at,
        test_set_entry_id, and test_set_execution_id, plus total count,
        offset, and limit.

    Raises:
        HTTPException 404: No test set exists with the given ID, no
            execution exists with the given ID, or the execution exists
            but doesn't belong to this test set.
    """
    await _find_test_set_or_404(test_set_id, session)
    await _check_test_set_execution_or_404(test_set_execution_id, session)
    await _check_test_set_execution_id_linked_to_specific_test_set_id_or_404(
        test_set_id, test_set_execution_id, session)

    total = await session.scalar(
        select(func.count(TestRunModel.id))
        .where(TestRunModel.test_set_execution_id == test_set_execution_id)
    ) or 0

    found = (await session.execute(
        select(
            TestRunModel.id,
            TestRunModel.status,
            TestRunModel.created_at,
            TestRunModel.test_set_entry_id,
        )
        .where(TestRunModel.test_set_execution_id == test_set_execution_id)
        .order_by(TestRunModel.created_at.desc(), TestRunModel.id.desc())
        .offset(offset)
        .limit(limit)
    )).all()

    return PaginatedTestSetExecutionRunMetadata(
        total=total,
        offset=offset,
        limit=limit,
        items=[
            TestSetExecutionRunMetadata(
                id=item.id,
                status=item.status,
                created_at=item.created_at,
                test_set_entry_id=TestSetEntryID(id=item.test_set_entry_id),
                test_set_execution_id=TestSetExecutionID(id=test_set_execution_id),
            )
            for item in found
        ]
    )


async def get_test_plan_execution_run_metadata_all_runs(
        test_plan_id: uuid.UUID,
        test_plan_execution_id: uuid.UUID,
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedTestPlanExecutionRunMetadata:
    """Orchestrates run listing for a single test plan execution: validates the
    test plan ID, the execution ID, and that the execution belongs to this
    test plan, counts total runs the execution produced, fetches the
    requested page, and returns a paginated response.

    Args:
        test_plan_id: The UUID of the test plan the execution must belong to.
        test_plan_execution_id: The UUID of the execution whose runs are being listed.
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with each run's ID, status, created_at,
        test_set_entry_id, and test_plan_execution_id, plus total count,
        offset, and limit.

    Raises:
        HTTPException 404: No test plan exists with the given ID, no
            execution exists with the given ID, or the execution exists
            but doesn't belong to this test plan.
    """
    await _find_test_plan_by_id_or_404(test_plan_id, session)
    await _check_test_plan_execution_or_404(test_plan_execution_id, session)
    await _check_test_plan_execution_id_linked_to_specific_test_plan_id_or_404(
        test_plan_id, test_plan_execution_id, session
    )

    total = await session.scalar(
        select(func.count(TestRunModel.id))
        .where(TestRunModel.test_plan_execution_id == test_plan_execution_id)
    ) or 0
    
    found = (await session.execute(
        select(
            TestRunModel.id,
            TestRunModel.status,
            TestRunModel.created_at,
            TestRunModel.test_set_entry_id,
        )
        .where(TestRunModel.test_plan_execution_id == test_plan_execution_id)
        .order_by(TestRunModel.created_at.desc(), TestRunModel.id.desc())
        .offset(offset)
        .limit(limit)
    )).all()
    
    return PaginatedTestPlanExecutionRunMetadata(
        total=total,
        offset=offset,
        limit=limit,
        items=[
            TestPlanExecutionRunMetadata(
                id=item.id,
                status=item.status,
                created_at=item.created_at,
                test_set_entry_id=TestSetEntryID(id=item.test_set_entry_id),
                test_plan_execution_id=TestPlanExecutionID(id=test_plan_execution_id),
            )
            for item in found
        ]
    )
