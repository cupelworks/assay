"""What every batch endpoint shares: finding a batch, loading its runs and the
entries they ran, its progress and spend, bringing its status up to date, and
computing its result the first time a read finds every run finished
(docs/version_1/statistics/dev_notes.md note 19, decision 4; note 21).
"""
import logging
import uuid
from collections import defaultdict
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import (
    IN_PROGRESS_BATCH_STATUSES,
    JUDGE_ENGINE,
    TERMINAL_STATUSES,
    BatchStatus,
    StandaloneRunModel,
    StatisticalBatchModel,
    TestModel,
    TestPlanModel,
    TestRunModel,
    TestSetEntryModel,
    TestSetModel,
    TestStatus,
    TestTypesModel,
)
from assay.schemas.statistics import (
    BatchCallCount,
    BatchCalls,
    BatchDetails,
    BatchProgress,
    BatchResult,
    BatchStatusName,
    BatchSummary,
    EntryProgress,
    RunCounts,
    Scope,
    ScopeKind,
    StatisticalEngine,
    WaveProgress,
)
from assay.services.statistics import compute
from assay.services.statistics.compute import entry_order

logger = logging.getLogger(__name__)

STOP_REASON = "Stopped before it ran: the batch was stopped"


async def find_batch_or_404(batch_id: uuid.UUID, session: AsyncSession) -> StatisticalBatchModel:
    batch = await session.get(StatisticalBatchModel, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Statistical batch with ID {batch_id} not found")
    return batch


# ── loading ──────────────────────────────────────────────────────────────────


async def load_entries(batch: StatisticalBatchModel, session: AsyncSession, *,
                       with_results: bool = True) -> list[compute.BatchEntry]:
    """The batch's entries with their runs in time order: the frozen content
    each run scored (a set entry, or a standalone run's copy of the test) —
    the same for every run of an entry, since a batch creates them together.
    `with_results=False` leaves the runs' results out (the heavy column): the
    runs matrix while a batch runs needs only each run's status."""
    columns = [TestRunModel.id, TestRunModel.batch_index, TestRunModel.status,
               TestRunModel.error, TestRunModel.test_set_entry_id,
               TestRunModel.test_set_execution_id, TestRunModel.test_plan_execution_id]
    if with_results:
        columns.append(TestRunModel.results)
    rows = (await session.execute(
        select(*columns).where(TestRunModel.batch_id == batch.id)
        .order_by(TestRunModel.batch_index)
    )).all()
    by_entry: dict[uuid.UUID | None, list[compute.BatchRun]] = defaultdict(list)
    for row in rows:
        by_entry[row.test_set_entry_id].append(compute.BatchRun(
            id=row.id, index=row.batch_index,
            execution_id=row.test_set_execution_id or row.test_plan_execution_id,
            status=row.status, results=row.results if with_results else None, error=row.error,
        ))
    entries = await frozen_entries(batch, list(by_entry), session)
    for entry in entries:
        entry.runs = by_entry[entry.entry_id]
    return entries


async def frozen_entries(batch: StatisticalBatchModel, entry_ids: list[uuid.UUID | None],
                         session: AsyncSession) -> list[compute.BatchEntry]:
    """The batch's entries without their runs: what each one is, its checks,
    whether its answer is recorded — in the one entry order statistics use."""
    if batch.test_id is not None:
        copy = await session.scalar(
            select(StandaloneRunModel)
            .join(TestRunModel, TestRunModel.id == StandaloneRunModel.id)
            .where(TestRunModel.batch_id == batch.id).limit(1))
        if copy is None:
            return []
        return [compute.BatchEntry(
            entry_id=None, test_id=batch.test_id, test_set_id=None, test_set_name=None,
            name=copy.name, recorded_answer=copy.model_output is not None,
            assignments=compute.frozen_assignments(copy.test_type_assignments),
        )]
    found = (await session.execute(
        select(TestSetEntryModel, TestSetModel.name)
        .outerjoin(TestSetModel, TestSetModel.id == TestSetEntryModel.test_set_id)
        .where(TestSetEntryModel.id.in_([key for key in entry_ids if key is not None]))
    )).all()
    return sorted((compute.set_entry(entry, set_name) for entry, set_name in found),
                  key=entry_order)


async def load_types(entries: list[compute.BatchEntry],
                     session: AsyncSession) -> dict[str, TestTypesModel]:
    names = {a.name for entry in entries for a in entry.assignments}
    rows = (await session.scalars(
        select(TestTypesModel).where(TestTypesModel.name.in_(names)))).all()
    return {row.name: row for row in rows}


# ── progress ─────────────────────────────────────────────────────────────────


async def load_progress(batch: StatisticalBatchModel, session: AsyncSession,
                        entries: list[compute.BatchEntry] | None = None,
                        types: dict[str, TestTypesModel] | None = None) -> BatchProgress:
    """How far the batch has got, from counts the database computes — no run
    and no result is loaded, so polling a 10,000-run batch costs a few small
    queries. `entries` and `types` are reused when the caller has them."""
    cancelled = and_(TestRunModel.status == TestStatus.not_ran,
                     TestRunModel.error == STOP_REASON)
    by_entry = (await session.execute(
        select(TestRunModel.test_set_entry_id, TestRunModel.status,
               func.count().label("runs"),
               func.sum(case((cancelled, 1), else_=0)).label("cancelled"))
        .where(TestRunModel.batch_id == batch.id)
        .group_by(TestRunModel.test_set_entry_id, TestRunModel.status)
    )).all()
    by_time = (await session.execute(
        select(func.sum(case((TestRunModel.status.in_(_IN_FLIGHT), 1), else_=0)).label("open"),
               func.sum(case((cancelled, 1), else_=0)).label("cancelled"),
               func.count().label("runs"))
        .where(TestRunModel.batch_id == batch.id)
        .group_by(TestRunModel.batch_index)
    )).all()
    if entries is None:
        entries = await frozen_entries(batch, list({row.test_set_entry_id for row in by_entry}),
                                       session)
    if types is None:
        types = await load_types(entries, session)
    _, left_out = compute.read_overrides(batch.overrides)
    judge_checks = {
        entry.entry_id: sum(types.get(a.name) is not None and types[a.name].engine == JUDGE_ENGINE
                            and (entry.entry_id, a.label) not in left_out
                            for a in entry.assignments)
        for entry in entries
    }
    asks_application = {entry.entry_id: not entry.recorded_answer for entry in entries}

    counts = RunCounts()
    application = {"finished": 0, "in_flight": 0}
    judge = {"finished": 0, "in_flight": 0}
    runs_cancelled = 0
    for row in by_entry:
        setattr(counts, row.status.value, getattr(counts, row.status.value) + row.runs)
        runs_cancelled += row.cancelled or 0
        asks, judges = asks_application.get(row.test_set_entry_id, False), judge_checks.get(
            row.test_set_entry_id, 0)
        if row.status == TestStatus.running:
            application["in_flight"] += asks * row.runs
            judge["in_flight"] += judges * row.runs
        elif row.status in TERMINAL_STATUSES:
            # an attempt counts, a failed one too; a run a stop cancelled made none,
            # and a Not Ran run never reached its checks
            attempted = row.runs - (row.cancelled or 0)
            application["finished"] += asks * attempted
            if row.status != TestStatus.not_ran:
                judge["finished"] += judges * row.runs

    per_time = batch.plan["calls_per_time"]
    waves = None
    if batch.waves_released is not None:
        looks = batch.plan["waves"]["looks"]
        waves = WaveProgress(
            released=batch.waves_released, planned=len(looks), looks=looks,
            closed=batch.waves_closed_at is not None,
            stopped_early=batch.waves_closed_at is not None and batch.stopped_at is None
            and batch.waves_released < len(looks))
    return BatchProgress(
        waves=waves,
        times_requested=batch.times_requested,
        times_done=sum(not row.open for row in by_time),
        runs_total=sum(row.runs for row in by_entry),
        runs_done=sum(row.runs for row in by_entry if row.status in TERMINAL_STATUSES),
        runs=counts, runs_cancelled=runs_cancelled,
        times_cancelled=sum(row.cancelled == row.runs for row in by_time),
        calls=BatchCalls(
            application=BatchCallCount(
                planned=per_time["application"] * batch.times_requested, **application),
            judge=BatchCallCount(planned=per_time["judge"] * batch.times_requested, **judge),
        ),
    )


def live_entries(entries: list[compute.BatchEntry]) -> list[EntryProgress]:
    """The runs matrix as it fills in — each entry's runs by time, Pending and
    Running included — for the batch page while the batch runs."""
    return [
        EntryProgress(entry_id=entry.entry_id, test_id=entry.test_id,
                      test_set_id=entry.test_set_id, test_set_name=entry.test_set_name,
                      name=entry.name, runs=compute.run_counts(entry.runs),
                      strip=compute.strip(entry.runs))
        for entry in entries
    ]


# ── keeping a batch up to date ───────────────────────────────────────────────


_IN_FLIGHT = (TestStatus.pending, TestStatus.running)


async def _live_status(batch: StatisticalBatchModel, session: AsyncSession) -> BatchStatus | None:
    """Pending while no run has started, Running while any is pending or
    running; None once every run has finished. One small grouped count."""
    statuses = dict((await session.execute(
        select(TestRunModel.status, func.count())
        .where(TestRunModel.batch_id == batch.id).group_by(TestRunModel.status)
    )).all())
    if not any(status in _IN_FLIGHT for status in statuses):
        return None
    if set(statuses) == {TestStatus.pending}:
        return BatchStatus.pending
    return BatchStatus.running


async def refresh(batch: StatisticalBatchModel, session: AsyncSession) -> None:
    """Bring an in-progress batch up to date: its status follows its runs, and
    the first refresh that finds every run finished computes the result and
    stores it. The write is conditional (only while no result is stored), so
    two reads racing store one result. While the batch runs this costs one
    grouped count; the runs are loaded, results included, only to compute the
    result — once.
    """
    if batch.status not in IN_PROGRESS_BATCH_STATUSES:
        return
    live = await _live_status(batch, session)
    if live is None and batch.waves_released is not None and batch.waves_closed_at is None:
        # between two waves of a batch that runs until there's an answer: the
        # worker is deciding on the next one, so it's still running
        live = BatchStatus.running
    if live is not None:
        if live != batch.status:
            previous = batch.status
            changed = await session.execute(
                update(StatisticalBatchModel)
                .where(StatisticalBatchModel.id == batch.id,
                       StatisticalBatchModel.status.in_(IN_PROGRESS_BATCH_STATUSES))
                .values(status=live))
            await session.commit()
            await session.refresh(batch)
            if changed.rowcount:
                logger.info("Batch %s is now %s (was %s)", batch.id, live.value,
                            previous.value,
                            extra={"batch_id": batch.id, "status": live.value,
                                   "previous_status": previous.value})
        return

    entries = await load_entries(batch, session)
    types = await load_types(entries, session)
    runs = [run for entry in entries for run in entry.runs]
    stopped = batch.stopped_at is not None
    now = datetime.now().astimezone()
    engine = StatisticalEngine(batch.engine)
    result = compute.compute(engine, batch.parameters, batch.plan["floor"], stopped, entries,
                             types, now, batch.overrides, batch.plan.get("waves"))
    final = compute.roll_up(result, stopped, runs, engine)
    done = await load_progress(batch, session, entries, types)
    result.summary = compute.summary(final, result,
                                     batch.times_requested - done.times_cancelled,
                                     batch.times_requested, runs)
    stored = await session.execute(
        update(StatisticalBatchModel)
        .where(StatisticalBatchModel.id == batch.id, StatisticalBatchModel.result.is_(None))
        .values(status=final, completed_at=now,
                result={"progress": done.model_dump(mode="json"),
                        "result": result.model_dump(mode="json")}))
    await session.commit()
    await session.refresh(batch)
    if stored.rowcount:
        logger.info(
            "Computed batch %s: %s, %d of %d applicable checks proven, %d failed",
            batch.id, final.value, result.verdicts["pass"], result.checks_applicable,
            result.verdicts["fail"],
            extra={"batch_id": batch.id, "status": final.value,
                   "verdicts": result.verdicts},
        )


# ── the response ─────────────────────────────────────────────────────────────


def scope_kind(batch: StatisticalBatchModel) -> tuple[ScopeKind, uuid.UUID]:
    if batch.test_id is not None:
        return ScopeKind.test, batch.test_id
    if batch.test_set_id is not None:
        return ScopeKind.test_set, batch.test_set_id
    return ScopeKind.test_plan, batch.test_plan_id


async def scope_names(batches: list[StatisticalBatchModel],
                      session: AsyncSession) -> dict[uuid.UUID, str]:
    """The current names of the batches' scopes, in three queries at most."""
    names: dict[uuid.UUID, str] = {}
    for model, column in ((TestModel, "test_id"), (TestSetModel, "test_set_id"),
                          (TestPlanModel, "test_plan_id")):
        ids = {getattr(b, column) for b in batches if getattr(b, column) is not None}
        if ids:
            rows = await session.execute(select(model.id, model.name).where(model.id.in_(ids)))
            names.update({row.id: row.name for row in rows})
    return names


async def describe(batch: StatisticalBatchModel, session: AsyncSession, *, series: bool = True,
                   with_result: bool = True, names: dict[uuid.UUID, str] | None = None,
                   ) -> BatchSummary | BatchDetails:
    """The batch as the API returns it. While the batch runs, its progress comes
    from counts (load_progress), and the single-batch read (`with_result`, with
    `series`) adds the runs matrix so far in `progress.entries` — each run's
    status, never its results; lists don't, and a stored result has the matrix
    in its own entries. A finished batch reads its stored progress."""
    if names is None:
        names = await scope_names([batch], session)
    kind, scope_id = scope_kind(batch)
    stored = batch.result
    if stored is not None:
        done = BatchProgress.model_validate(stored["progress"])
    else:
        done = await load_progress(batch, session)
        if with_result and series:
            done.entries = live_entries(await load_entries(batch, session, with_results=False))
    fields = dict(
        id=batch.id,
        scope=Scope(kind=kind, id=scope_id, name=names.get(scope_id, "")),
        statistical_test=batch.statistical_test, engine=StatisticalEngine(batch.engine),
        parameters=batch.parameters, note=batch.note,
        status=BatchStatusName(batch.status.value), floor=batch.plan["floor"],
        progress=done,
        summary=stored["result"]["summary"] if stored else None,
        verdicts=stored["result"]["verdicts"] if stored else None,
        created_at=batch.created_at, stopped_at=batch.stopped_at,
        completed_at=batch.completed_at,
        targets=(batch.overrides or {}).get("targets", []),
        leave_out=(batch.overrides or {}).get("leave_out", []),
    )
    if not with_result:
        return BatchSummary(**fields)
    result = None
    if stored is not None:
        result = BatchResult.model_validate(stored["result"])
        if not series:
            for entry in result.entries:
                entry.strip = None
                for check in entry.checks:
                    check.series = None
    return BatchDetails(**fields, result=result)
