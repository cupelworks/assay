# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""One execution of a test set or plan, read whole (its runs counted and its
checks gathered), and its runs listed with their names, checks and sorts.
Sets and plans differ only in their tables, guards and id fields, which an
`ExecutionKind` names, so every function here serves both."""
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import (
    StandaloneRunModel,
    TestPlanExecutionModel,
    TestRunModel,
    TestSetEntryModel,
    TestSetExecutionModel,
)
from assay.schemas import (
    ExecutionRunSort,
    PaginatedTestPlanExecutionRunMetadata,
    PaginatedTestSetExecutionRunMetadata,
    RunCounts,
    TestPlanExecutionDetails,
    TestPlanExecutionID,
    TestPlanExecutionRunItem,
    TestPlanID,
    TestPlanReplayedExecutionID,
    TestSetEntryID,
    TestSetExecutionDetails,
    TestSetExecutionID,
    TestSetExecutionRunItem,
    TestSetID,
    TestSetReplayedExecutionID,
)
from assay.services._listing import Listing
from assay.services.runs._common import (
    _check_test_plan_execution_id_linked_to_specific_test_plan_id_or_404,
    _check_test_plan_execution_or_404,
    _check_test_set_execution_id_linked_to_specific_test_set_id_or_404,
    _check_test_set_execution_or_404,
)
from assay.services.runs._summaries import (
    STATUS_RANK,
    TEST_NAME,
    count_checks,
    execution_checks,
    run_source,
)
from assay.services.test_plans._common import _find_test_plan_by_id_or_404
from assay.services.test_sets._common import _find_test_set_or_404


@dataclass(frozen=True)
class ExecutionKind:
    model: type
    run_column: object  # the run's foreign key to this kind of execution
    find_scope: Callable[[uuid.UUID, AsyncSession], Awaitable]
    find_execution: Callable[[uuid.UUID, AsyncSession], Awaitable]
    check_link: Callable[[uuid.UUID, uuid.UUID, AsyncSession], Awaitable]
    execution_field: Callable[[uuid.UUID], dict]  # a run item's execution id field
    scope_field: Callable[[uuid.UUID], dict]  # an execution's scope id field
    replayed: Callable[[uuid.UUID], object]
    run_item: type
    page: type
    details: type


TEST_SET = ExecutionKind(
    model=TestSetExecutionModel, run_column=TestRunModel.test_set_execution_id,
    find_scope=_find_test_set_or_404, find_execution=_check_test_set_execution_or_404,
    check_link=_check_test_set_execution_id_linked_to_specific_test_set_id_or_404,
    execution_field=lambda id_: {"test_set_execution_id": TestSetExecutionID(id=id_)},
    scope_field=lambda id_: {"test_set_id": TestSetID(id=id_)},
    replayed=lambda id_: TestSetReplayedExecutionID(id=id_),
    run_item=TestSetExecutionRunItem, page=PaginatedTestSetExecutionRunMetadata,
    details=TestSetExecutionDetails,
)
TEST_PLAN = ExecutionKind(
    model=TestPlanExecutionModel, run_column=TestRunModel.test_plan_execution_id,
    find_scope=_find_test_plan_by_id_or_404, find_execution=_check_test_plan_execution_or_404,
    check_link=_check_test_plan_execution_id_linked_to_specific_test_plan_id_or_404,
    execution_field=lambda id_: {"test_plan_execution_id": TestPlanExecutionID(id=id_)},
    scope_field=lambda id_: {"test_plan_id": TestPlanID(id=id_)},
    replayed=lambda id_: TestPlanReplayedExecutionID(id=id_),
    run_item=TestPlanExecutionRunItem, page=PaginatedTestPlanExecutionRunMetadata,
    details=TestPlanExecutionDetails,
)

_RUNS = Listing(
    key=TestRunModel.id,
    source=run_source,
    sorts={ExecutionRunSort.newest: (TestRunModel.created_at.desc(),),
           ExecutionRunSort.worst_first: (STATUS_RANK, func.lower(TEST_NAME)),
           ExecutionRunSort.name: (func.lower(TEST_NAME),)},
)


async def _guard(kind: ExecutionKind, scope_id: uuid.UUID, execution_id: uuid.UUID,
                 session: AsyncSession):
    """The test set or plan, after a 404 unless it exists, the execution
    exists, and the execution is its."""
    scope = await kind.find_scope(scope_id, session)
    await kind.find_execution(execution_id, session)
    await kind.check_link(scope_id, execution_id, session)
    return scope


async def list_execution_runs(kind: ExecutionKind, scope_id: uuid.UUID,
                              execution_id: uuid.UUID, session: AsyncSession,
                              sort: ExecutionRunSort = ExecutionRunSort.newest,
                              offset: int = 0, limit: int = 100):
    """An execution's runs, sorted and paged, each with its test's name, its
    checks counted, its error and where its answer came from."""
    await _guard(kind, scope_id, execution_id, session)
    where = [kind.run_column == execution_id]
    found = (await session.execute(_RUNS.page(
        select(TestRunModel.id, TestRunModel.status, TestRunModel.created_at,
               TestRunModel.batch_id, TestRunModel.batch_index, TestRunModel.test_set_entry_id,
               TestRunModel.results, TestRunModel.error, TestRunModel.output_source,
               TEST_NAME.label("test_name")),
        where, sort, offset, limit))).all()
    return kind.page(
        total=await _RUNS.count(session, where), offset=offset, limit=limit,
        items=[kind.run_item(
            id=row.id, status=row.status, created_at=row.created_at, batch_id=row.batch_id,
            batch_index=row.batch_index, test_set_entry_id=TestSetEntryID(id=row.test_set_entry_id),
            test_name=row.test_name, checks=count_checks(row.results), error=row.error,
            output_source=row.output_source, **kind.execution_field(execution_id))
            for row in found])


async def get_execution(kind: ExecutionKind, scope_id: uuid.UUID, execution_id: uuid.UUID,
                        session: AsyncSession):
    """One execution: when it started, what it replayed, its runs by status,
    and its checks met, not met and not run; named after its set or plan."""
    scope = await _guard(kind, scope_id, execution_id, session)
    execution = await session.get(kind.model, execution_id)
    runs = (await session.execute(run_source(
        select(TestRunModel.id, TestRunModel.status, TestRunModel.results, TestRunModel.error,
               TestRunModel.skip_labels, TEST_NAME.label("test_name"),
               func.coalesce(StandaloneRunModel.test_type_assignments,
                             TestSetEntryModel.test_type_assignments).label("assignments")))
        .where(kind.run_column == execution_id)
        .order_by(STATUS_RANK, func.lower(TEST_NAME), TestRunModel.id))).all()
    counts = RunCounts()
    for run in runs:
        counts.add(run.status.value)
    return kind.details(
        id=execution.id, name=scope.name, created_at=execution.created_at,
        batch_id=execution.batch_id, batch_index=execution.batch_index, run_count=len(runs),
        runs=counts,
        replayed_execution_id=(kind.replayed(execution.replayed_execution_id)
                               if execution.replayed_execution_id else None),
        checks=execution_checks(runs), **kind.scope_field(scope_id))
