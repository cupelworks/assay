"""What earlier runs showed about each check of a scope — the history the
estimate plans a batch from (docs/statistics/dev_notes.md notes 30–32).

A check's history is its results in the runs that asked exactly the question
the batch would ask: a set entry's runs (its content is frozen once it has
run, so they all did), or a standalone test's runs whose frozen copy matches
the test as it is now. Batches' runs count like any other; runs that were
Not Ran, and results whose check couldn't run (`errored`), say nothing about
the check and are left out. Only the most recent runs count, since the
application behind them changes.
"""
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import StandaloneRunModel, TestRunModel, TestStatus
from assay.services.statistics.compute import BatchEntry
from assay.services.tests._common import (
    _find_all_tests_with_details_or_404,
    _frozen_test_type_assignments,
)

# the most recent runs per entry that count
HISTORY_RUNS = 200
# a standalone test's runs are scanned newest first, this many at most, for
# the ones whose copy matches the test now
_STANDALONE_SCAN = 1000
_EVALUATED = (TestStatus.green, TestStatus.amber, TestStatus.red)

HistoryKey = tuple[uuid.UUID | None, str]


@dataclass
class CheckHistory:
    """One check's results over its recent runs: how many decided it, how
    many of those passed, and its scores when it gives one."""
    runs: int = 0
    passed: int = 0
    scores: list[float] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return self.runs - self.passed

    def add(self, result: dict) -> None:
        if result.get("errored") or result.get("passed") is None:
            return
        self.runs += 1
        self.passed += bool(result["passed"])
        if isinstance(result.get("score"), int | float):
            self.scores.append(float(result["score"]))


async def load_history(entries: list[BatchEntry],
                       session: AsyncSession) -> dict[HistoryKey, CheckHistory]:
    """Every check of the scope's entries, keyed by (entry id, label) — the
    entry id is None for a standalone test. A check that never gave a result
    has no key."""
    histories: dict[HistoryKey, CheckHistory] = {}
    set_entries = [entry.entry_id for entry in entries if entry.entry_id is not None]
    if set_entries:
        ranked = (
            select(TestRunModel.test_set_entry_id.label("entry_id"),
                   TestRunModel.results.label("results"),
                   func.row_number().over(
                       partition_by=TestRunModel.test_set_entry_id,
                       order_by=(TestRunModel.executed_at.desc(), TestRunModel.id.desc()),
                   ).label("rank"))
            .where(TestRunModel.test_set_entry_id.in_(set_entries),
                   TestRunModel.status.in_(_EVALUATED),
                   TestRunModel.results.is_not(None))
            .subquery()
        )
        rows = (await session.execute(
            select(ranked.c.entry_id, ranked.c.results).where(ranked.c.rank <= HISTORY_RUNS)
        )).all()
        for row in rows:
            _add(histories, row.entry_id, row.results)
    for entry in entries:
        if entry.entry_id is None:
            for results in await _standalone_results(entry.test_id, session):
                _add(histories, None, results)
    return histories


def _add(histories: dict[HistoryKey, CheckHistory], entry_id: uuid.UUID | None,
         results: dict) -> None:
    for label, result in (results or {}).items():
        if isinstance(result, dict):
            histories.setdefault((entry_id, label), CheckHistory()).add(result)


async def _standalone_results(test_id: uuid.UUID, session: AsyncSession) -> list[dict]:
    """The results of the test's recent runs whose frozen copy asked what the
    test asks now: the same input, expected output, recorded answer and
    checks. A run of the test before an edit answered another question."""
    (test,) = await _find_all_tests_with_details_or_404([test_id], session)
    now = (test.input, test.expected_output, test.model_output,
           _frozen_test_type_assignments(test))
    rows = (await session.execute(
        select(TestRunModel.results, StandaloneRunModel.input,
               StandaloneRunModel.expected_output, StandaloneRunModel.model_output,
               StandaloneRunModel.test_type_assignments)
        .join(StandaloneRunModel, StandaloneRunModel.id == TestRunModel.id)
        .where(TestRunModel.test_id == test_id,
               TestRunModel.status.in_(_EVALUATED),
               TestRunModel.results.is_not(None))
        .order_by(TestRunModel.executed_at.desc(), TestRunModel.id.desc())
        .limit(_STANDALONE_SCAN)
    )).all()
    matching = [row.results for row in rows
                if (row.input, row.expected_output, row.model_output,
                    _same_checks(row.test_type_assignments)) == now]
    return matching[:HISTORY_RUNS]


def _same_checks(frozen: list[dict] | None) -> list[dict]:
    """A copy's checks in the shape and order the test's are compared in
    (copies saved before `answer_path` existed lack the key)."""
    return [{"name": item.get("name"), "label": item.get("label"),
             "config": item.get("config"), "answer_path": item.get("answer_path")}
            for item in frozen or []]
