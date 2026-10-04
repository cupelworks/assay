"""What run and execution lists show without opening a run: the test it asked
and the set or plan it ran in (joined from its frozen copy and its
execution), its checks counted, runs counted by status, and the worst-first
order."""
from collections.abc import Iterable, Mapping

from sqlalchemy import ColumnElement, Select, case, func

from assay.models import (
    StandaloneRunModel,
    TestPlanExecutionModel,
    TestPlanModel,
    TestRunModel,
    TestSetEntryModel,
    TestSetExecutionModel,
    TestSetModel,
    TestStatus,
)
from assay.run_check_types import asked_checks
from assay.schemas import (
    ExecutionCheckNotMet,
    ExecutionChecks,
    ExecutionRunNotRan,
    RunChecks,
)


def run_source(statement: Select) -> Select:
    """A run with what names it: its standalone copy or set entry, its
    execution, and that execution's set or plan (each an outer join, at most
    one side matching)."""
    return (statement.select_from(TestRunModel)
            .outerjoin(StandaloneRunModel, StandaloneRunModel.id == TestRunModel.id)
            .outerjoin(TestSetEntryModel, TestSetEntryModel.id == TestRunModel.test_set_entry_id)
            .outerjoin(TestSetExecutionModel,
                       TestSetExecutionModel.id == TestRunModel.test_set_execution_id)
            .outerjoin(TestPlanExecutionModel,
                       TestPlanExecutionModel.id == TestRunModel.test_plan_execution_id)
            .outerjoin(TestSetModel, TestSetModel.id == TestSetExecutionModel.test_set_id)
            .outerjoin(TestPlanModel, TestPlanModel.id == TestPlanExecutionModel.test_plan_id))


TEST_NAME: ColumnElement = func.coalesce(StandaloneRunModel.name, TestSetEntryModel.name)
# the test a run asked: the live test of a standalone run, else the test its entry was copied from
TEST_ID: ColumnElement = func.coalesce(TestRunModel.test_id, TestSetEntryModel.test_id)
SCOPE_NAME: ColumnElement = func.coalesce(TestSetModel.name, TestPlanModel.name)

# what needs looking at first, then what met every check, then what's still under way
WORST_FIRST = (TestStatus.not_ran, TestStatus.red, TestStatus.amber, TestStatus.green,
               TestStatus.running, TestStatus.pending)
# compared through the column, so each status is matched as it's stored
STATUS_RANK: ColumnElement = case(*((TestRunModel.status == status, rank)
                                    for rank, status in enumerate(WORST_FIRST)))


def count_checks(results: Mapping | None) -> RunChecks | None:
    """A run's checks counted from its results (by label): met when passed;
    failed or errored, not met. None before the run has results."""
    if results is None:
        return None
    not_met = [label for label, result in results.items()
               if not (result.get("passed") and not result.get("errored"))]
    return RunChecks(met=len(results) - len(not_met), total=len(results), not_met=not_met)


def execution_checks(runs: Iterable) -> ExecutionChecks:
    """An execution's checks over its runs (each with id, test_name, status,
    results, error, the frozen `assignments` and `skip_labels`): every check
    met counted, every one not met listed with its run, every run that
    couldn't run listed with how many checks it would have asked."""
    met, not_met, not_ran = 0, [], []
    for run in runs:
        if run.status == TestStatus.not_ran:
            not_ran.append(ExecutionRunNotRan(
                run_id=run.id, test_name=run.test_name, error=run.error,
                checks=len(asked_checks(run.assignments, run.skip_labels))))
            continue
        checks = count_checks(run.results)
        if checks is None:
            continue
        met += checks.met
        not_met.extend(ExecutionCheckNotMet(run_id=run.id, test_name=run.test_name, label=label)
                       for label in checks.not_met)
    return ExecutionChecks(met=met, not_met=not_met, not_ran=not_ran)
