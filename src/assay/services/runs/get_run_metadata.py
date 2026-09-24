import uuid

from sqlalchemy import func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import TestPlanExecutionModel, TestRunModel, TestSetExecutionModel, TestStatus
from assay.schemas import (
    ExecutionMetadata,
    ExecutionOrigin,
    PaginatedExecutionMetadata,
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


_ORIGIN_FK_COLUMN = {
    RunOrigin.standalone: TestRunModel.test_id,
    RunOrigin.test_set: TestRunModel.test_set_execution_id,
    RunOrigin.test_plan: TestRunModel.test_plan_execution_id,
}


async def get_run_metadata_all_runs(
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
        status: TestStatus | None = None,
        origin: RunOrigin | None = None,
) -> PaginatedRunMetadata:
    """Orchestrates run listing across the entire system, regardless of
    origin: counts every TestRunModel row in scope, fetches the requested
    page, and returns a paginated response with each row's origin resolved
    from TestRunModel's own mode invariant.

    Unlike every other listing in this module, this has no parent resource
    to validate — no guard runs first. With neither `status` nor `origin`
    given, every run is in scope, whether it was created standalone, via a
    test set execution, or via a test plan execution. A single query over
    TestRunModel covers all three, since which mode a row belongs to is
    already fully determined by which of its own FK columns is set (see
    TestRunModel's docstring) — no join needed to tell them apart, and
    filtering by `origin` is just an IS NOT NULL check on the one FK column
    that mode implies, same reasoning. The one place this does join:
    resolving `test_set_id`/`test_plan_id` (so a caller can deep-link a row
    without first resolving its execution ID) needs two LEFT OUTER JOINs
    against TestSetExecutionModel/TestPlanExecutionModel, since neither ID
    lives on TestRunModel itself — harmless per row since at most one side
    ever matches, same mode invariant.

    Args:
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.
        status: If given, restricts both the count and the page to runs
            currently at this status (e.g. `Pending`, to see what's still
            queued). `None` (the default) returns every run regardless of
            status, the original unfiltered behavior.
        origin: If given, restricts both the count and the page to runs
            created this way (`Standalone`, `TestSet`, or `TestPlan`).
            `None` (the default) returns every run regardless of origin.
            Combines with `status` — both filters apply together when both
            are given.

    Returns:
        A paginated response with each run's ID, status, created_at,
        origin, and the origin-specific ID(s) that follow from it (exactly
        one of `test_case_id`, or the `test_set_entry_id` +
        `test_set_execution_id`/`test_set_id` pair, or the
        `test_set_entry_id` + `test_plan_execution_id`/`test_plan_id`
        pair, is non-null per item), plus total count, offset, and limit.
    """
    count_stmt = select(func.count(TestRunModel.id))
    found_stmt = select(
        TestRunModel.id,
        TestRunModel.status,
        TestRunModel.created_at,
        TestRunModel.test_id,
        TestRunModel.test_set_entry_id,
        TestRunModel.test_set_execution_id,
        TestRunModel.test_plan_execution_id,
        TestSetExecutionModel.test_set_id,
        TestPlanExecutionModel.test_plan_id,
    ).outerjoin(
        TestSetExecutionModel,
        TestRunModel.test_set_execution_id == TestSetExecutionModel.id,
    ).outerjoin(
        TestPlanExecutionModel,
        TestRunModel.test_plan_execution_id == TestPlanExecutionModel.id,
    )
    if status is not None:
        count_stmt = count_stmt.where(TestRunModel.status == status)
        found_stmt = found_stmt.where(TestRunModel.status == status)
    if origin is not None:
        fk_column = _ORIGIN_FK_COLUMN[origin]
        count_stmt = count_stmt.where(fk_column.is_not(None))
        found_stmt = found_stmt.where(fk_column.is_not(None))

    total = await session.scalar(count_stmt) or 0

    found = (await session.execute(
        found_stmt
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
            test_set_entry_id, test_set_execution_id,
            test_plan_execution_id, test_set_id (from the
            TestSetExecutionModel join), and test_plan_id (from the
            TestPlanExecutionModel join).

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
            test_set_id=None,
            test_plan_id=None,
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
            test_set_id=TestSetID(id=row.test_set_id),
            test_plan_id=None,
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
        test_set_id=None,
        test_plan_id=TestPlanID(id=row.test_plan_id),
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


async def get_execution_metadata_all_executions(
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
) -> PaginatedExecutionMetadata:
    """Orchestrates execution listing across the entire system, regardless of
    origin: fetches the top `offset + limit` most recent executions from
    each of TestSetExecutionModel and TestPlanExecutionModel (each with its
    own run_count aggregated exactly like the scoped listings above), merges
    the two streams in Python, and returns the requested page.

    No parent resource to validate — no guard runs first, same as
    get_run_metadata_all_runs. Unlike that function, this can't be answered
    by a single unfiltered query: TestSetExecutionModel and
    TestPlanExecutionModel are two separate tables (an execution has no
    shared base table to select across, unlike a run's mode columns all
    living on TestRunModel), so a correctly globally-ordered, paginated feed
    across both requires fetching enough of each side and merging.

    Fetching the top `offset + limit` rows from each side (ordered
    created_at descending, same tiebreak as everywhere else) is sufficient
    to guarantee correctness: in the fully-merged global ordering, any item
    ranked within the first `offset + limit` positions can have at most
    `offset + limit - 1` items ranked before it, so it must itself already
    be within its own table's top `offset + limit` rows. Slicing the merged,
    re-sorted result to `[offset : offset + limit]` then gives the correct
    page. This avoids a cross-table SQL UNION (portability risk between the
    SQLite used locally and the Postgres this is meant to run against in
    production) at the cost of over-fetching from whichever side is not
    well-represented near the top — acceptable at the pagination sizes this
    API already uses elsewhere (default limit 100, no deep-pagination use
    case for this endpoint).

    Args:
        session: Async SQLAlchemy session injected by FastAPI.
        offset: Number of records to skip.
        limit: Maximum number of records to return.

    Returns:
        A paginated response with each execution's ID, created_at, origin,
        run_count, and the origin-specific ID(s) that follow from it
        (`test_set_id` + `replayed_test_set_execution_id` for `TestSet`,
        `test_plan_id` + `replayed_test_plan_execution_id` for `TestPlan`),
        plus total count, offset, and limit.
    """
    test_set_total = await session.scalar(
        select(func.count(TestSetExecutionModel.id))
    ) or 0
    test_plan_total = await session.scalar(
        select(func.count(TestPlanExecutionModel.id))
    ) or 0
    total = test_set_total + test_plan_total

    fetch_count = offset + limit

    test_set_rows = (await session.execute(
        select(
            TestSetExecutionModel.id,
            TestSetExecutionModel.created_at,
            TestSetExecutionModel.test_set_id,
            TestSetExecutionModel.replayed_execution_id,
            func.count(TestRunModel.id).label("run_count"),
        )
        .join(
            TestRunModel,
            TestSetExecutionModel.id == TestRunModel.test_set_execution_id,
            isouter=True,
        )
        .group_by(TestSetExecutionModel.id)
        .order_by(TestSetExecutionModel.created_at.desc(), TestSetExecutionModel.id.desc())
        .limit(fetch_count)
    )).all()

    test_plan_rows = (await session.execute(
        select(
            TestPlanExecutionModel.id,
            TestPlanExecutionModel.created_at,
            TestPlanExecutionModel.test_plan_id,
            TestPlanExecutionModel.replayed_execution_id,
            func.count(TestRunModel.id).label("run_count"),
        )
        .join(
            TestRunModel,
            TestPlanExecutionModel.id == TestRunModel.test_plan_execution_id,
            isouter=True,
        )
        .group_by(TestPlanExecutionModel.id)
        .order_by(TestPlanExecutionModel.created_at.desc(), TestPlanExecutionModel.id.desc())
        .limit(fetch_count)
    )).all()

    merged = sorted(
        [_execution_metadata_from_test_set_row(row) for row in test_set_rows]
        + [_execution_metadata_from_test_plan_row(row) for row in test_plan_rows],
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )

    return PaginatedExecutionMetadata(
        total=total,
        offset=offset,
        limit=limit,
        items=merged[offset:offset + limit],
    )


def _execution_metadata_from_test_set_row(row: Row) -> ExecutionMetadata:
    """Build a `TestSet`-origin ExecutionMetadata from a TestSetExecutionModel row.

    Args:
        row: A result row carrying id, created_at, test_set_id,
            replayed_execution_id, and run_count.

    Returns:
        The row's ExecutionMetadata, with origin=TestSet, test_plan_id and
        replayed_test_plan_execution_id left null.
    """
    return ExecutionMetadata(
        id=row.id,
        created_at=row.created_at,
        origin=ExecutionOrigin.test_set,
        run_count=row.run_count,
        test_set_id=TestSetID(id=row.test_set_id),
        test_plan_id=None,
        replayed_test_set_execution_id=TestSetReplayedExecutionID(id=row.replayed_execution_id)
                                       if row.replayed_execution_id else None,
        replayed_test_plan_execution_id=None,
    )


def _execution_metadata_from_test_plan_row(row: Row) -> ExecutionMetadata:
    """Build a `TestPlan`-origin ExecutionMetadata from a TestPlanExecutionModel row.

    Args:
        row: A result row carrying id, created_at, test_plan_id,
            replayed_execution_id, and run_count.

    Returns:
        The row's ExecutionMetadata, with origin=TestPlan, test_set_id and
        replayed_test_set_execution_id left null.
    """
    return ExecutionMetadata(
        id=row.id,
        created_at=row.created_at,
        origin=ExecutionOrigin.test_plan,
        run_count=row.run_count,
        test_set_id=None,
        test_plan_id=TestPlanID(id=row.test_plan_id),
        replayed_test_set_execution_id=None,
        replayed_test_plan_execution_id=TestPlanReplayedExecutionID(id=row.replayed_execution_id)
                                        if row.replayed_execution_id else None,
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
