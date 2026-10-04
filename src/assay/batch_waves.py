"""The runs of a batch that runs in waves ("until there's an answer"): which
checks it judges, what each check's runs showed so far, and the runs of its
next wave. The worker advances such a batch after each wave; the API reads
the same answers back. Code both need, so it lives outside the API-only
code (a worker can't import services/).
"""
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from assay import sequential
from assay.assignment_labels import in_label_order, labelled
from assay.models import (
    JUDGE_ENGINE,
    StandaloneRunModel,
    StatisticalBatchModel,
    TestPlanExecutionModel,
    TestRunModel,
    TestSetEntryModel,
    TestSetExecutionModel,
    TestStatus,
    TestTypesModel,
)
from assay.run_check_types import with_check_types
from assay.schemas import TestTypeAssignment

SEQUENTIAL_ENGINES = frozenset({"sequential_gate", "sequential_judge_stability",
                                "sequential_t"})
_IN_FLIGHT = (TestStatus.pending, TestStatus.running)


@dataclass(frozen=True)
class SequentialCheck:
    """One check the batch judges: its entry (None for a standalone test),
    its label, its target, and whether its count is the judge's agreement —
    or, for an average, the threshold its scores are judged against."""
    entry_id: uuid.UUID | None
    label: str
    target: float | None
    agreement: bool
    threshold: float | None = None
    higher_is_better: bool = True


def read_overrides(stored: dict | None) -> tuple[dict, set]:
    """A batch's stored per-check targets and left-out checks, by (entry id,
    label) — the entry id a UUID, None for a standalone test."""
    def key(item: dict) -> tuple:
        entry = item.get("entry_id")
        return (uuid.UUID(str(entry)) if entry else None, item["label"])
    stored = stored or {}
    return ({key(item): float(item["target"]) for item in stored.get("targets", [])},
            {key(item) for item in stored.get("leave_out", [])})


def wave_plan_for(batch: StatisticalBatchModel, target: float) -> sequential.WavePlan:
    """The batch's looks, at the level calibrated for this check's target
    (a check with its own target keeps the batch's looks)."""
    plan = sequential.WavePlan.from_json(batch.plan["waves"])
    if target == batch.parameters["target"]:
        return plan
    level = sequential._calibrate(plan.looks, target, batch.parameters["confidence"])
    return sequential.WavePlan(looks=plan.looks, level=level)


def judged_checks(batch: StatisticalBatchModel, session: Session) -> list[SequentialCheck]:
    """The checks the batch gives an answer: every check but those left out,
    or, judging the judge, its judge checks on a recorded answer."""
    targets, left_out = read_overrides(batch.overrides)
    agreement = batch.engine == "sequential_judge_stability"
    average = batch.engine == "sequential_t"
    entries = _frozen_entries(batch, session)
    names = {a.name for _, _, assignments in entries for a in assignments}
    rows = {row.name: row for row in session.scalars(
        select(TestTypesModel).where(TestTypesModel.name.in_(names)))}
    checks = []
    for entry_id, recorded, assignments in entries:
        for assignment in assignments:
            key = (entry_id, assignment.label)
            row = rows.get(assignment.name)
            if key in left_out:
                continue
            if agreement and (row is None or row.engine != JUDGE_ENGINE or not recorded):
                continue
            if average:
                threshold = _threshold(assignment)
                if row is None or row.comparison is None or threshold is None:
                    continue
                checks.append(SequentialCheck(entry_id, assignment.label, None, False,
                                              threshold, row.comparison.value == "gte"))
                continue
            checks.append(SequentialCheck(entry_id, assignment.label,
                                          targets.get(key, batch.parameters["target"]),
                                          agreement))
    return checks


def _threshold(assignment: TestTypeAssignment) -> float | None:
    try:
        value = float((assignment.config or {}).get("threshold"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def scores(batch_id: uuid.UUID, session: Session) -> dict[tuple, list[tuple[int, float]]]:
    """Every scored check's scores so far, as (time, score), by (entry id,
    label)."""
    rows = session.execute(
        select(TestRunModel.test_set_entry_id, TestRunModel.batch_index, TestRunModel.results)
        .where(TestRunModel.batch_id == batch_id, TestRunModel.results.is_not(None))).all()
    found: dict[tuple, list[tuple[int, float]]] = defaultdict(list)
    for entry_id, index, results in rows:
        for label, result in (results or {}).items():
            if isinstance(result, dict) and not result.get("errored") \
                    and isinstance(result.get("score"), int | float):
                found[(entry_id, label)].append((index, float(result["score"])))
    return found


def outcomes(batch_id: uuid.UUID, session: Session) -> dict[tuple, list[tuple[int, bool]]]:
    """Every check's results so far, as (time, passed), by (entry id, label):
    the runs that gave it a result."""
    rows = session.execute(
        select(TestRunModel.test_set_entry_id, TestRunModel.batch_index, TestRunModel.results)
        .where(TestRunModel.batch_id == batch_id, TestRunModel.results.is_not(None))).all()
    found: dict[tuple, list[tuple[int, bool]]] = defaultdict(list)
    for entry_id, index, results in rows:
        for label, result in (results or {}).items():
            if isinstance(result, dict) and not result.get("errored") \
                    and result.get("passed") is not None:
                found[(entry_id, label)].append((index, bool(result["passed"])))
    return found


def in_flight(batch_id: uuid.UUID, session: Session) -> bool:
    """Whether any run of the batch is still pending or running."""
    return session.scalar(select(TestRunModel.id).where(
        TestRunModel.batch_id == batch_id, TestRunModel.status.in_(_IN_FLIGHT)).limit(1)) \
        is not None


def next_wave(batch: StatisticalBatchModel, session: Session, first: int, last: int
              ) -> tuple[list, list[TestRunModel]]:
    """The executions and runs of times first..last, the same content as the
    batch's first wave: a standalone test's frozen copy copied (the test may
    have been edited since), a set's or plan's entries run again; each run
    with its check types."""
    _, left_out = read_overrides(batch.overrides)
    skips: dict = defaultdict(list)
    for entry_id, label in left_out:
        skips[entry_id].append(label)
    now = datetime.now().astimezone()
    executions, runs = [], []
    if batch.test_id is not None:
        copy = session.scalar(
            select(StandaloneRunModel).join(TestRunModel, TestRunModel.id == StandaloneRunModel.id)
            .where(TestRunModel.batch_id == batch.id).order_by(TestRunModel.batch_index).limit(1))
        for index in range(first, last + 1):
            run = TestRunModel(id=uuid.uuid4(), test_id=batch.test_id, status=TestStatus.pending,
                               created_at=now, batch_id=batch.id, batch_index=index,
                               skip_labels=sorted(skips[None]) or None)
            run.standalone_run = StandaloneRunModel(
                id=run.id, name=copy.name, input=copy.input,
                expected_output=copy.expected_output, model_output=copy.model_output,
                test_type_assignments=copy.test_type_assignments, snapshot_at=copy.snapshot_at)
            runs.append(run)
        with_check_types(runs)
        return executions, runs
    entry_ids = sorted(set(session.scalars(
        select(TestRunModel.test_set_entry_id).where(TestRunModel.batch_id == batch.id))),
        key=str)
    for index in range(first, last + 1):
        if batch.test_set_id is not None:
            execution = TestSetExecutionModel(id=uuid.uuid4(), test_set_id=batch.test_set_id,
                                              created_at=now, batch_id=batch.id,
                                              batch_index=index)
            link = {"test_set_execution_id": execution.id}
        else:
            execution = TestPlanExecutionModel(id=uuid.uuid4(), test_plan_id=batch.test_plan_id,
                                               created_at=now, batch_id=batch.id,
                                               batch_index=index)
            link = {"test_plan_execution_id": execution.id}
        executions.append(execution)
        runs.extend(TestRunModel(id=uuid.uuid4(), status=TestStatus.pending, created_at=now,
                                 test_set_entry_id=entry_id, batch_id=batch.id,
                                 batch_index=index, skip_labels=sorted(skips[entry_id]) or None,
                                 **link)
                    for entry_id in entry_ids)
    with_check_types(runs, dict(session.execute(
        select(TestSetEntryModel.id, TestSetEntryModel.test_type_assignments)
        .where(TestSetEntryModel.id.in_(entry_ids))).tuples().all()))
    return executions, runs


def _frozen_entries(batch: StatisticalBatchModel, session: Session
                    ) -> list[tuple[uuid.UUID | None, bool, list[TestTypeAssignment]]]:
    """Each entry the batch runs: its id, whether its answer is recorded, its
    checks as frozen — labelled and in label order."""
    def assignments(frozen: list[dict] | None) -> list[TestTypeAssignment]:
        return in_label_order(labelled([TestTypeAssignment(**item) for item in frozen or []]))
    if batch.test_id is not None:
        copy = session.scalar(
            select(StandaloneRunModel).join(TestRunModel, TestRunModel.id == StandaloneRunModel.id)
            .where(TestRunModel.batch_id == batch.id).limit(1))
        return [] if copy is None else [(None, copy.model_output is not None,
                                         assignments(copy.test_type_assignments))]
    entry_ids = set(session.scalars(
        select(TestRunModel.test_set_entry_id).where(TestRunModel.batch_id == batch.id)))
    entries = session.scalars(select(TestSetEntryModel)
                              .where(TestSetEntryModel.id.in_(entry_ids))).all()
    return [(entry.id, entry.model_output is not None, assignments(entry.test_type_assignments))
            for entry in entries]
