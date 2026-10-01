"""What every batch endpoint shares: finding a batch, loading its runs and the
entries they ran, its progress and spend, bringing its status up to date, and
computing its result the first time a read finds every run finished
(docs/statistics/dev_notes.md note 19, decision 4; note 21).
"""
import logging
import uuid
from collections import defaultdict
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from assay.assignment_labels import in_label_order, labelled
from assay.models import (
    IN_PROGRESS_BATCH_STATUSES,
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
from assay.schemas import TestTypeAssignment
from assay.schemas.statistics import (
    BatchCallCount,
    BatchCalls,
    BatchDetails,
    BatchProgress,
    BatchResult,
    BatchStatusName,
    BatchSummary,
    Scope,
    ScopeKind,
    StatisticalTestName,
)
from assay.services.statistics import compute
from assay.services.statistics._scope import JUDGE_ENGINE

logger = logging.getLogger(__name__)

STOP_REASON = "Stopped before it ran: the batch was stopped"


async def find_batch_or_404(batch_id: uuid.UUID, session: AsyncSession) -> StatisticalBatchModel:
    batch = await session.get(StatisticalBatchModel, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Statistical batch with ID {batch_id} not found")
    return batch


# ── loading ──────────────────────────────────────────────────────────────────


async def load_entries(batch: StatisticalBatchModel,
                       session: AsyncSession) -> list[compute.BatchEntry]:
    """The batch's entries with their runs in time order: the frozen content
    each run scored (a set entry, or a standalone run's copy of the test) —
    the same for every run of an entry, since a batch creates them together."""
    rows = (await session.execute(
        select(TestRunModel.id, TestRunModel.batch_index, TestRunModel.status,
               TestRunModel.results, TestRunModel.error, TestRunModel.test_set_entry_id,
               TestRunModel.test_set_execution_id, TestRunModel.test_plan_execution_id)
        .where(TestRunModel.batch_id == batch.id)
        .order_by(TestRunModel.batch_index)
    )).all()
    by_entry: dict[uuid.UUID | None, list[compute.BatchRun]] = defaultdict(list)
    for row in rows:
        by_entry[row.test_set_entry_id].append(compute.BatchRun(
            id=row.id, index=row.batch_index,
            execution_id=row.test_set_execution_id or row.test_plan_execution_id,
            status=row.status, results=row.results, error=row.error,
        ))

    if batch.test_id is not None:
        runs = by_entry[None]
        copy = await session.get(StandaloneRunModel, runs[0].id) if runs else None
        if copy is None:
            return []
        return [compute.BatchEntry(
            entry_id=None, test_id=batch.test_id, test_set_id=None, test_set_name=None,
            name=copy.name, recorded_answer=copy.model_output is not None,
            assignments=_assignments(copy.test_type_assignments), runs=runs,
        )]

    found = (await session.execute(
        select(TestSetEntryModel, TestSetModel.name)
        .outerjoin(TestSetModel, TestSetModel.id == TestSetEntryModel.test_set_id)
        .where(TestSetEntryModel.id.in_([key for key in by_entry if key is not None]))
    )).all()
    entries = [
        compute.BatchEntry(
            entry_id=entry.id, test_id=entry.test_id, test_set_id=entry.test_set_id,
            test_set_name=set_name, name=entry.name,
            recorded_answer=entry.model_output is not None,
            assignments=_assignments(entry.test_type_assignments), runs=by_entry[entry.id],
        )
        for entry, set_name in found
    ]
    return sorted(entries, key=lambda e: ((e.test_set_name or "").casefold(),
                                          e.name.casefold(), str(e.entry_id)))


def _assignments(frozen: list[dict]) -> list[TestTypeAssignment]:
    return in_label_order(labelled([TestTypeAssignment(**item) for item in frozen or []]))


async def load_types(entries: list[compute.BatchEntry],
                     session: AsyncSession) -> dict[str, TestTypesModel]:
    names = {a.name for entry in entries for a in entry.assignments}
    rows = (await session.scalars(
        select(TestTypesModel).where(TestTypesModel.name.in_(names)))).all()
    return {row.name: row for row in rows}


# ── progress ─────────────────────────────────────────────────────────────────


def _cancelled(run: compute.BatchRun) -> bool:
    return run.status == TestStatus.not_ran and run.error == STOP_REASON


def progress(batch: StatisticalBatchModel, entries: list[compute.BatchEntry],
             types: dict[str, TestTypesModel]) -> BatchProgress:
    runs = [run for entry in entries for run in entry.runs]
    by_time: dict[int, list[compute.BatchRun]] = defaultdict(list)
    for run in runs:
        by_time[run.index].append(run)
    times_done = sum(all(run.status in TERMINAL_STATUSES for run in time)
                     for time in by_time.values())
    times_cancelled = sum(all(_cancelled(run) for run in time) for time in by_time.values())

    per_time = batch.plan["calls_per_time"]
    application = {"finished": 0, "in_flight": 0}
    judge = {"finished": 0, "in_flight": 0}
    for entry in entries:
        judge_checks = sum(
            types.get(a.name) is not None and types[a.name].engine == JUDGE_ENGINE
            for a in entry.assignments)
        for run in entry.runs:
            if run.status == TestStatus.running:
                application["in_flight"] += not entry.recorded_answer
                judge["in_flight"] += judge_checks
            elif run.status in TERMINAL_STATUSES and not _cancelled(run):
                application["finished"] += not entry.recorded_answer
                judge["finished"] += sum(
                    result.get("engine") == JUDGE_ENGINE
                    for result in (run.results or {}).values())

    return BatchProgress(
        times_requested=batch.times_requested, times_done=times_done,
        runs_total=len(runs),
        runs_done=sum(run.status in TERMINAL_STATUSES for run in runs),
        runs=compute.run_counts(runs),
        runs_cancelled=sum(_cancelled(run) for run in runs),
        times_cancelled=times_cancelled,
        calls=BatchCalls(
            application=BatchCallCount(
                planned=per_time["application"] * batch.times_requested, **application),
            judge=BatchCallCount(planned=per_time["judge"] * batch.times_requested, **judge),
        ),
    )


# ── keeping a batch up to date ───────────────────────────────────────────────


def _live_status(runs: list[compute.BatchRun]) -> BatchStatus | None:
    """Pending or Running while any run is; None once every run has finished."""
    if not any(run.status not in TERMINAL_STATUSES for run in runs):
        return None
    if all(run.status == TestStatus.pending for run in runs):
        return BatchStatus.pending
    return BatchStatus.running


async def refresh(batch: StatisticalBatchModel, session: AsyncSession
                  ) -> tuple[list[compute.BatchEntry], dict[str, TestTypesModel]] | None:
    """Bring an in-progress batch up to date: its status follows its runs, and
    the first refresh that finds every run finished computes the result and
    stores it. The write is conditional (only while no result is stored), so
    two reads racing store one result. Returns the loaded entries and types,
    or None for a batch already completed (its stored result is the answer).
    """
    if batch.status not in IN_PROGRESS_BATCH_STATUSES:
        return None
    entries = await load_entries(batch, session)
    types = await load_types(entries, session)
    runs = [run for entry in entries for run in entry.runs]
    live = _live_status(runs)
    if live is not None:
        if live != batch.status:
            await session.execute(
                update(StatisticalBatchModel)
                .where(StatisticalBatchModel.id == batch.id,
                       StatisticalBatchModel.status.in_(IN_PROGRESS_BATCH_STATUSES))
                .values(status=live))
            await session.commit()
            await session.refresh(batch)
        return entries, types

    stopped = batch.stopped_at is not None
    now = datetime.now().astimezone()
    name = StatisticalTestName(batch.statistical_test)
    result = compute.compute(name, batch.parameters, batch.plan["floor"], stopped, entries,
                             types, now)
    final = compute.roll_up(result, stopped, runs)
    done = progress(batch, entries, types)
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
    return entries, types


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
                   loaded=None) -> BatchSummary | BatchDetails:
    """The batch as the API returns it. `loaded` is what refresh() returned, to
    spare loading the runs twice."""
    if names is None:
        names = await scope_names([batch], session)
    kind, scope_id = scope_kind(batch)
    stored = batch.result
    if stored is not None:
        done = BatchProgress.model_validate(stored["progress"])
    else:
        entries, types = loaded or (None, None)
        if entries is None:
            entries = await load_entries(batch, session)
            types = await load_types(entries, session)
        done = progress(batch, entries, types)
    fields = dict(
        id=batch.id,
        scope=Scope(kind=kind, id=scope_id, name=names.get(scope_id, "")),
        statistical_test=StatisticalTestName(batch.statistical_test),
        parameters=batch.parameters, note=batch.note,
        status=BatchStatusName(batch.status.value), floor=batch.plan["floor"],
        progress=done,
        summary=stored["result"]["summary"] if stored else None,
        created_at=batch.created_at, stopped_at=batch.stopped_at,
        completed_at=batch.completed_at,
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
