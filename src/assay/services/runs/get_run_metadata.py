# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import uuid

from sqlalchemy import func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import (
    TestPlanExecutionModel,
    TestPlanModel,
    TestRunModel,
    TestSetExecutionModel,
    TestSetModel,
)
from assay.schemas import (
    ExecutionMetadata,
    ExecutionOrigin,
    ExecutionRunSort,
    PaginatedExecutionMetadata,
    PaginatedStandaloneRunCreationMetadata,
    PaginatedTestPlanExecutionMetadata,
    PaginatedTestPlanExecutionRunMetadata,
    PaginatedTestSetExecutionMetadata,
    PaginatedTestSetExecutionRunMetadata,
    RunCounts,
    StandaloneRunCreationMetadata,
    TestCaseID,
    TestPlanExecutionDetails,
    TestPlanExecutionMetadata,
    TestPlanID,
    TestPlanReplayedExecutionID,
    TestSetExecutionDetails,
    TestSetExecutionMetadata,
    TestSetID,
    TestSetReplayedExecutionID,
)
from assay.services._standing import run_counts_by
from assay.services.runs._common import (
    _batch_clause,
)
from assay.services.runs.executions import (
    TEST_PLAN,
    TEST_SET,
    get_execution,
    list_execution_runs,
)
from assay.services.test_plans._common import _find_test_plan_by_id_or_404
from assay.services.test_sets._common import _find_test_set_or_404
from assay.services.tests._common import _find_test_by_id_or_404


async def get_standalone_run_metadata_all_test_runs(
        test_id: uuid.UUID,
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
        batch: str | None = None,
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
    filters = [TestRunModel.test_id == test_id]
    if (clause := _batch_clause(TestRunModel.batch_id, batch)) is not None:
        filters.append(clause)

    total = await session.scalar(
        select(func.count(TestRunModel.id)).where(*filters)
    ) or 0

    found = (await session.execute(
        select(TestRunModel.id, TestRunModel.status, TestRunModel.created_at,
               TestRunModel.batch_id, TestRunModel.batch_index)
        .where(*filters)
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
                batch_id=item.batch_id,
                batch_index=item.batch_index,
            )
            for item in found
        ],
    )


async def get_test_set_execution_metadata_all_executions(
        test_set_id: uuid.UUID,
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
        batch: str | None = None,
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
    
    filters = [TestSetExecutionModel.test_set_id == test_set_id]
    if (clause := _batch_clause(TestSetExecutionModel.batch_id, batch)) is not None:
        filters.append(clause)

    total = await session.scalar(
        select(func.count(TestSetExecutionModel.id)).where(*filters)
    ) or 0

    found = (await session.execute(
        select(
            TestSetExecutionModel.id,
            TestSetExecutionModel.created_at,
            TestSetExecutionModel.replayed_execution_id,
            TestSetExecutionModel.batch_id,
            TestSetExecutionModel.batch_index,
            func.count(TestRunModel.id).label("run_count"),
        )
        .join(
            TestRunModel,
            TestSetExecutionModel.id == TestRunModel.test_set_execution_id,
            isouter=True,
        )
        .where(*filters)
        .group_by(TestSetExecutionModel.id)
        .order_by(TestSetExecutionModel.created_at.desc(), TestSetExecutionModel.id.desc())
        .offset(offset)
        .limit(limit)
    )).all()
    
    runs = await run_counts_by(session, TestRunModel.test_set_execution_id,
                               [item.id for item in found])

    return PaginatedTestSetExecutionMetadata(
        total=total,
        offset=offset,
        limit=limit,
        items=[
            TestSetExecutionMetadata(
                id=item.id,
                runs=runs[item.id],
                created_at=item.created_at,
                test_set_id=TestSetID(id=test_set_id),
                run_count=item.run_count,
                replayed_execution_id=TestSetReplayedExecutionID(id=item.replayed_execution_id)
                                      if item.replayed_execution_id else None,
                batch_id=item.batch_id,
                batch_index=item.batch_index,
            )
            for item in found
        ]
    )


async def get_test_plan_execution_metadata_all_executions(
        test_plan_id: uuid.UUID,
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
        batch: str | None = None,
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

    filters = [TestPlanExecutionModel.test_plan_id == test_plan_id]
    if (clause := _batch_clause(TestPlanExecutionModel.batch_id, batch)) is not None:
        filters.append(clause)

    total = await session.scalar(
        select(func.count(TestPlanExecutionModel.id)).where(*filters)
    ) or 0

    found = (await session.execute(
        select(
            TestPlanExecutionModel.id,
            TestPlanExecutionModel.created_at,
            TestPlanExecutionModel.replayed_execution_id,
            TestPlanExecutionModel.batch_id,
            TestPlanExecutionModel.batch_index,
            func.count(TestRunModel.id).label("run_count"),
        )
        .join(
            TestRunModel,
            TestPlanExecutionModel.id == TestRunModel.test_plan_execution_id,
            isouter=True,
        )
        .where(*filters)
        .group_by(TestPlanExecutionModel.id)
        .order_by(TestPlanExecutionModel.created_at.desc(), TestPlanExecutionModel.id.desc())
        .offset(offset)
        .limit(limit)
    )).all()

    runs = await run_counts_by(session, TestRunModel.test_plan_execution_id,
                               [item.id for item in found])

    return PaginatedTestPlanExecutionMetadata(
        total=total,
        offset=offset,
        limit=limit,
        items=[
            TestPlanExecutionMetadata(
                id=item.id,
                runs=runs[item.id],
                created_at=item.created_at,
                test_plan_id=TestPlanID(id=test_plan_id),
                run_count=item.run_count,
                replayed_execution_id=TestPlanReplayedExecutionID(id=item.replayed_execution_id)
                                      if item.replayed_execution_id else None,
                batch_id=item.batch_id,
                batch_index=item.batch_index,
            )
            for item in found
        ]
    )


async def get_execution_metadata_all_executions(
        session: AsyncSession,
        offset: int = 0,
        limit: int = 100,
        batch: str | None = None,
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
    set_filters, plan_filters = [], []
    if batch is not None:
        set_filters.append(_batch_clause(TestSetExecutionModel.batch_id, batch))
        plan_filters.append(_batch_clause(TestPlanExecutionModel.batch_id, batch))
    test_set_total = await session.scalar(
        select(func.count(TestSetExecutionModel.id)).where(*set_filters)
    ) or 0
    test_plan_total = await session.scalar(
        select(func.count(TestPlanExecutionModel.id)).where(*plan_filters)
    ) or 0
    total = test_set_total + test_plan_total

    fetch_count = offset + limit

    test_set_rows = (await session.execute(
        select(
            TestSetExecutionModel.id,
            TestSetExecutionModel.created_at,
            TestSetExecutionModel.test_set_id,
            TestSetExecutionModel.replayed_execution_id,
            TestSetExecutionModel.batch_id,
            TestSetExecutionModel.batch_index,
            TestSetModel.name,
            func.count(TestRunModel.id).label("run_count"),
        )
        .join(TestSetModel, TestSetModel.id == TestSetExecutionModel.test_set_id)
        .join(
            TestRunModel,
            TestSetExecutionModel.id == TestRunModel.test_set_execution_id,
            isouter=True,
        )
        .where(*set_filters)
        .group_by(TestSetExecutionModel.id, TestSetModel.name)
        .order_by(TestSetExecutionModel.created_at.desc(), TestSetExecutionModel.id.desc())
        .limit(fetch_count)
    )).all()

    test_plan_rows = (await session.execute(
        select(
            TestPlanExecutionModel.id,
            TestPlanExecutionModel.created_at,
            TestPlanExecutionModel.test_plan_id,
            TestPlanExecutionModel.replayed_execution_id,
            TestPlanExecutionModel.batch_id,
            TestPlanExecutionModel.batch_index,
            TestPlanModel.name,
            func.count(TestRunModel.id).label("run_count"),
        )
        .join(TestPlanModel, TestPlanModel.id == TestPlanExecutionModel.test_plan_id)
        .join(
            TestRunModel,
            TestPlanExecutionModel.id == TestRunModel.test_plan_execution_id,
            isouter=True,
        )
        .where(*plan_filters)
        .group_by(TestPlanExecutionModel.id, TestPlanModel.name)
        .order_by(TestPlanExecutionModel.created_at.desc(), TestPlanExecutionModel.id.desc())
        .limit(fetch_count)
    )).all()

    page = sorted(
        [(row, _execution_metadata_from_test_set_row) for row in test_set_rows]
        + [(row, _execution_metadata_from_test_plan_row) for row in test_plan_rows],
        key=lambda found: (found[0].created_at, found[0].id),
        reverse=True,
    )[offset:offset + limit]
    set_runs = await run_counts_by(session, TestRunModel.test_set_execution_id,
                                   [row.id for row, build in page
                                    if build is _execution_metadata_from_test_set_row])
    plan_runs = await run_counts_by(session, TestRunModel.test_plan_execution_id,
                                    [row.id for row, build in page
                                     if build is _execution_metadata_from_test_plan_row])
    runs = set_runs | plan_runs

    return PaginatedExecutionMetadata(
        total=total,
        offset=offset,
        limit=limit,
        items=[build(row, runs[row.id]) for row, build in page],
    )


def _execution_metadata_from_test_set_row(row: Row, runs: RunCounts) -> ExecutionMetadata:
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
        batch_id=row.batch_id,
        batch_index=row.batch_index,
        created_at=row.created_at,
        origin=ExecutionOrigin.test_set,
        name=row.name,
        runs=runs,
        run_count=row.run_count,
        test_set_id=TestSetID(id=row.test_set_id),
        test_plan_id=None,
        replayed_test_set_execution_id=TestSetReplayedExecutionID(id=row.replayed_execution_id)
                                       if row.replayed_execution_id else None,
        replayed_test_plan_execution_id=None,
    )


def _execution_metadata_from_test_plan_row(row: Row, runs: RunCounts) -> ExecutionMetadata:
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
        batch_id=row.batch_id,
        batch_index=row.batch_index,
        created_at=row.created_at,
        origin=ExecutionOrigin.test_plan,
        name=row.name,
        runs=runs,
        run_count=row.run_count,
        test_set_id=None,
        test_plan_id=TestPlanID(id=row.test_plan_id),
        replayed_test_set_execution_id=None,
        replayed_test_plan_execution_id=TestPlanReplayedExecutionID(id=row.replayed_execution_id)
                                        if row.replayed_execution_id else None,
    )


async def get_test_set_execution_run_metadata_all_runs(
        test_set_id: uuid.UUID, test_set_execution_id: uuid.UUID, session: AsyncSession,
        offset: int = 0, limit: int = 100, sort: ExecutionRunSort = ExecutionRunSort.newest,
) -> PaginatedTestSetExecutionRunMetadata:
    """A test set execution's runs, sorted and paged (`executions.list_execution_runs`).

    Raises:
        HTTPException 404: No test set exists with the given ID, no
            execution exists with the given ID, or the execution exists
            but doesn't belong to this test set.
    """
    return await list_execution_runs(TEST_SET, test_set_id, test_set_execution_id, session,
                                     sort, offset, limit)


async def get_test_plan_execution_run_metadata_all_runs(
        test_plan_id: uuid.UUID, test_plan_execution_id: uuid.UUID, session: AsyncSession,
        offset: int = 0, limit: int = 100, sort: ExecutionRunSort = ExecutionRunSort.newest,
) -> PaginatedTestPlanExecutionRunMetadata:
    """A test plan execution's runs, sorted and paged (`executions.list_execution_runs`).

    Raises:
        HTTPException 404: No test plan exists with the given ID, no
            execution exists with the given ID, or the execution exists
            but doesn't belong to this test plan.
    """
    return await list_execution_runs(TEST_PLAN, test_plan_id, test_plan_execution_id, session,
                                     sort, offset, limit)


async def get_test_set_execution_details(test_set_id: uuid.UUID, test_set_execution_id: uuid.UUID,
                                         session: AsyncSession) -> TestSetExecutionDetails:
    """One test set execution, read whole (`executions.get_execution`): 404 as the
    execution's runs list."""
    return await get_execution(TEST_SET, test_set_id, test_set_execution_id, session)


async def get_test_plan_execution_details(test_plan_id: uuid.UUID,
                                          test_plan_execution_id: uuid.UUID,
                                          session: AsyncSession) -> TestPlanExecutionDetails:
    """One test plan execution, read whole (`executions.get_execution`): 404 as the
    execution's runs list."""
    return await get_execution(TEST_PLAN, test_plan_id, test_plan_execution_id, session)

