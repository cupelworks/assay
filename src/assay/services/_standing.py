# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""How a page of tests, test sets or test plans stands, in a fixed number of
queries per page, never one per item: counts per key, the keys that have
something, and the latest row per key. The delete guards ask the same "has
runs" questions here, so what a list shows and what a delete refuses agree."""
import uuid
from collections.abc import Callable, Iterable, Sequence

from sqlalchemy import ColumnElement, Select, case, distinct, exists, func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import (
    DatasetRowModel,
    StatisticalBatchModel,
    TestModel,
    TestPlanExecutionModel,
    TestRunModel,
    TestSetEntryModel,
    TestSetExecutionModel,
    TestStatus,
)
from assay.schemas import RunCounts
from assay.schemas.standing import (
    LatestBatch,
    LatestExecution,
    LatestRun,
    ScopeStanding,
    TestStanding,
)

Join = Callable[[Select], Select]


def _as_is(statement: Select) -> Select:
    return statement


async def count_by(session: AsyncSession, column: ColumnElement, keys: Iterable,
                   *where: ColumnElement, join: Join = _as_is) -> dict:
    """Rows per key of `column`, among `keys`; every key present, 0 when none."""
    keys = list(keys)
    counts = dict.fromkeys(keys, 0)
    if keys:
        rows = await session.execute(join(select(column, func.count()))
                                     .where(column.in_(keys), *where).group_by(column))
        counts.update(rows.tuples().all())
    return counts


async def keys_with(session: AsyncSession, column: ColumnElement, keys: Iterable,
                    *where: ColumnElement, join: Join = _as_is) -> set:
    """The keys among `keys` that `column` holds in at least one row."""
    keys = list(keys)
    if not keys:
        return set()
    return set((await session.scalars(join(select(distinct(column)))
                                      .where(column.in_(keys), *where))).all())


async def latest_per(session: AsyncSession, key: ColumnElement, keys: Iterable,
                     columns: Sequence[ColumnElement], order: Sequence[ColumnElement],
                     *where: ColumnElement) -> dict[object, Row]:
    """The first row per key of `key` among `keys`, by `order`, with `columns`."""
    keys = list(keys)
    if not keys:
        return {}
    ranked = (select(key.label("key"), *columns,
                     func.row_number().over(partition_by=key, order_by=order).label("rank"))
              .where(key.in_(keys), *where).subquery())
    rows = await session.execute(select(ranked).where(ranked.c.rank == 1))
    return {row.key: row for row in rows}


async def run_counts_by(session: AsyncSession, column: ColumnElement,
                        keys: Iterable[uuid.UUID]) -> dict[uuid.UUID, RunCounts]:
    """Runs by status for each key of `column` (an execution's id, say), in
    one grouped query; every key present, all zeros when it has no runs."""
    keys = list(keys)
    counts = {key: RunCounts() for key in keys}
    if not keys:
        return counts
    rows = await session.execute(
        select(column, TestRunModel.status, func.count(TestRunModel.id))
        .where(column.in_(keys)).group_by(column, TestRunModel.status))
    for key, status, runs in rows.all():
        counts[key].add(status.value, runs)
    return counts


# --- runs, the delete guards' questions too ---


async def tests_with_runs(session: AsyncSession, ids: Iterable[uuid.UUID]) -> set:
    """Tests with any standalone run, a batch's included."""
    return await keys_with(session, TestRunModel.test_id, ids)


def _entry_runs(statement: Select) -> Select:
    return statement.join(TestRunModel, TestRunModel.test_set_entry_id == TestSetEntryModel.id)


async def sets_with_runs(session: AsyncSession, ids: Iterable[uuid.UUID]) -> set:
    """Test sets one of whose entries has a run, a batch's included."""
    return await keys_with(session, TestSetEntryModel.test_set_id, ids, join=_entry_runs)


async def plans_with_runs(session: AsyncSession, ids: Iterable[uuid.UUID]) -> set:
    """Test plans with any execution, a batch's included."""
    return await keys_with(session, TestPlanExecutionModel.test_plan_id, ids)


async def entries_with_runs(session: AsyncSession, ids: Iterable[uuid.UUID]) -> set:
    """Set entries with a run: frozen, they can't be edited or deleted."""
    return await keys_with(session, TestRunModel.test_set_entry_id, ids)


# --- the latest of each ---

_NEWEST_BATCH = (StatisticalBatchModel.created_at.desc(), StatisticalBatchModel.id.desc())


async def refresh_batches(session: AsyncSession, *where: ColumnElement) -> None:
    """Bring the in-progress batches matching `where` up to date, so their
    stored status is the truth: a batch's status only moves on when it's read."""
    # imported here: the statistics services lead back to this module
    from assay.services.statistics._batches import refresh_in_progress
    await refresh_in_progress(session, *where)


async def latest_batches(session: AsyncSession, scope_column: ColumnElement,
                         ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, LatestBatch]:
    """Each scope's newest batch (`scope_column`: the batch's test, set or plan
    id), its in-progress batches brought up to date first."""
    ids = list(ids)
    if ids:
        await refresh_batches(session, scope_column.in_(ids))
    rows = await latest_per(session, scope_column, ids,
                            (StatisticalBatchModel.id, StatisticalBatchModel.status,
                             StatisticalBatchModel.created_at), _NEWEST_BATCH)
    return {key: LatestBatch(id=row.id, status=row.status.value, created_at=row.created_at)
            for key, row in rows.items()}


async def latest_runs(session: AsyncSession, test_ids: Iterable[uuid.UUID]
                      ) -> dict[uuid.UUID, LatestRun]:
    """Each test's newest standalone run outside a batch."""
    rows = await latest_per(session, TestRunModel.test_id, test_ids,
                            (TestRunModel.id, TestRunModel.status, TestRunModel.created_at),
                            (TestRunModel.created_at.desc(), TestRunModel.id.desc()),
                            TestRunModel.batch_id.is_(None))
    return {key: LatestRun(id=row.id, status=row.status, created_at=row.created_at)
            for key, row in rows.items()}


async def scope_standing(session: AsyncSession, model: type, ids: Iterable[uuid.UUID]
                         ) -> dict[uuid.UUID, ScopeStanding]:
    """How each test set (`model` TestSetExecutionModel) or test plan
    (TestPlanExecutionModel) of a page stands."""
    ids = list(ids)
    is_set = model is TestSetExecutionModel
    scope = model.test_set_id if is_set else model.test_plan_id
    run_column = (TestRunModel.test_set_execution_id if is_set
                  else TestRunModel.test_plan_execution_id)
    outside_batch = model.batch_id.is_(None)
    executions = await latest_per(session, scope, ids,
                                  (model.id, model.created_at, model.replayed_execution_id),
                                  (model.created_at.desc(), model.id.desc()), outside_batch)
    runs = await run_counts_by(session, run_column, [row.id for row in executions.values()])
    counts = await count_by(session, scope, ids, outside_batch)
    batch_scope = (StatisticalBatchModel.test_set_id if is_set
                   else StatisticalBatchModel.test_plan_id)
    batches = await latest_batches(session, batch_scope, ids)
    with_runs = await (sets_with_runs if is_set else plans_with_runs)(session, ids)
    return {id_: ScopeStanding(
        latest_batch=batches.get(id_),
        latest_execution=(LatestExecution(id=row.id, created_at=row.created_at,
                                          replayed=row.replayed_execution_id is not None,
                                          runs=runs[row.id])
                          if (row := executions.get(id_)) else None),
        execution_count=counts[id_], has_runs=id_ in with_runs) for id_ in ids}


async def test_standing(session: AsyncSession, tests: Sequence[TestModel]
                        ) -> dict[uuid.UUID, TestStanding]:
    """How each test of a page stands, and the dataset row it came from."""
    ids = [test.id for test in tests]
    row_ids = {test.dataset_row_id for test in tests if test.dataset_row_id is not None}
    numbers = dict((await session.execute(
        select(DatasetRowModel.id, DatasetRowModel.position)
        .where(DatasetRowModel.id.in_(row_ids)))).tuples().all()) if row_ids else {}
    batches = await latest_batches(session, StatisticalBatchModel.test_id, ids)
    runs = await latest_runs(session, ids)
    with_runs = await tests_with_runs(session, ids)
    copies = await count_by(session, TestSetEntryModel.test_id, ids)
    holding = await count_by(session, TestSetEntryModel.test_id, ids,
                             TestSetEntryModel.test_set_id.is_not(None))
    return {test.id: TestStanding(
        created_at=test.created_at, dataset_row_id=test.dataset_row_id,
        dataset_row_number=numbers.get(test.dataset_row_id),
        latest_batch=batches.get(test.id), latest_run=runs.get(test.id),
        has_runs=test.id in with_runs, copy_count=copies[test.id],
        test_set_count=holding[test.id]) for test in tests}


# --- the same, in SQL: what the lists filter, sort and count by ---
# Each is a correlated subquery tied explicitly to the outer row's table: nested
# inside an EXISTS, auto-correlation wouldn't reach it, and every row would see
# the newest of all.


def latest_batch_sql(scope_column: ColumnElement, key: ColumnElement, column: ColumnElement):
    """`column` of the newest batch whose `scope_column` is `key` (correlated)."""
    return (select(column).where(scope_column == key).order_by(*_NEWEST_BATCH).limit(1)
            .correlate(key.table).scalar_subquery())


def latest_test_run_sql(test_key: ColumnElement, column: ColumnElement):
    """`column` of a test's newest standalone run outside a batch (correlated)."""
    return (select(column).where(TestRunModel.test_id == test_key,
                                 TestRunModel.batch_id.is_(None))
            .order_by(TestRunModel.created_at.desc(), TestRunModel.id.desc()).limit(1)
            .correlate(test_key.table).scalar_subquery())


def latest_execution_sql(model: type, scope_column: ColumnElement, key: ColumnElement,
                         column: ColumnElement):
    """`column` of a set's or plan's newest execution outside a batch (correlated)."""
    return (select(column).where(scope_column == key, model.batch_id.is_(None))
            .order_by(model.created_at.desc(), model.id.desc()).limit(1)
            .correlate(key.table).scalar_subquery())


IN_PROGRESS = "Running"
# an execution's outcome: in progress while a run is, else its worst run's status
_OUTCOME_ORDER = (TestStatus.not_ran, TestStatus.red, TestStatus.amber, TestStatus.green)


def execution_outcome_sql(run_column: ColumnElement, execution_id) -> ColumnElement:
    """An execution's one status: `Running` while any of its runs is Pending or
    Running, else the first of NotRan, Red, Amber, Green any run has (the
    API's spelling); null when there's no execution."""
    def any_run(*statuses):
        return exists().where(run_column == execution_id, TestRunModel.status.in_(statuses))
    return case((any_run(TestStatus.pending, TestStatus.running), IN_PROGRESS),
                *((any_run(status), status.value) for status in _OUTCOME_ORDER),
                else_=None)
