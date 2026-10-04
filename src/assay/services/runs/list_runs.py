"""Every run in the system, filtered, searched, sorted and paged, and the same
runs counted by status and by origin: one declaration (`RUNS`) for both."""
import uuid
from dataclasses import dataclass

from sqlalchemy import case, exists, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import (
    TestPlanExecutionModel,
    TestRunCheckTypeModel,
    TestRunModel,
    TestSetExecutionModel,
    TestStatus,
)
from assay.schemas import (
    PaginatedRunMetadata,
    RunFacets,
    RunMetadata,
    RunOrigin,
    RunSort,
    TestCaseID,
    TestPlanExecutionID,
    TestPlanID,
    TestSetEntryID,
    TestSetExecutionID,
    TestSetID,
)
from assay.services._listing import Facet, ListFilters, Listing, contains_text, created_between
from assay.services.runs._common import _batch_clause
from assay.services.runs._summaries import (
    SCOPE_NAME,
    TEST_ID,
    TEST_NAME,
    count_checks,
    run_source,
)

_ORIGIN_FK_COLUMN = {
    RunOrigin.standalone: TestRunModel.test_id,
    RunOrigin.test_set: TestRunModel.test_set_execution_id,
    RunOrigin.test_plan: TestRunModel.test_plan_execution_id,
}
# a run's origin follows from which of its own columns is set
_ORIGIN = case((TestRunModel.test_id.is_not(None), RunOrigin.standalone.value),
               (TestRunModel.test_set_execution_id.is_not(None), RunOrigin.test_set.value),
               else_=RunOrigin.test_plan.value)

RUNS = Listing(
    key=TestRunModel.id,
    source=run_source,
    filters={
        "status": lambda values: TestRunModel.status.in_(values),
        "origin": lambda values: or_(*(_ORIGIN_FK_COLUMN[o].is_not(None) for o in values)),
        "batch": lambda values: _batch_clause(TestRunModel.batch_id, values[0]),
        "test_set_id": lambda values: TestSetExecutionModel.test_set_id.in_(values),
        "test_plan_id": lambda values: TestPlanExecutionModel.test_plan_id.in_(values),
        "check_type": lambda values: exists().where(
            TestRunCheckTypeModel.run_id == TestRunModel.id,
            TestRunCheckTypeModel.test_type_name.in_(values)),
        "created": lambda values: created_between(TestRunModel.created_at, *values),
    },
    search=lambda q: contains_text(q, TEST_NAME, SCOPE_NAME),
    sorts={RunSort.newest: (TestRunModel.created_at.desc(),),
           RunSort.oldest: (TestRunModel.created_at,)},
    facets={"status": Facet(TestRunModel.status, values=[s.value for s in TestStatus]),
            "origin": Facet(_ORIGIN, values=[o.value for o in RunOrigin])},
)


@dataclass(frozen=True)
class RunFilters(ListFilters):
    """The runs list's filters, as the API received them. A filter given
    several times means any of its values; filters narrow together."""
    status: list[TestStatus] | None = None
    origin: list[RunOrigin] | None = None
    batch: str | None = None
    test_set_id: list[uuid.UUID] | None = None
    test_plan_id: list[uuid.UUID] | None = None
    check_type: list[str] | None = None

    def chosen(self) -> dict:
        return {"status": self.status, "origin": self.origin,
                "batch": [self.batch] if self.batch else None,
                "test_set_id": self.test_set_id, "test_plan_id": self.test_plan_id,
                "check_type": self.check_type, "created": self.created()}


async def get_run_metadata_all_runs(session: AsyncSession, filters: RunFilters | None = None,
                                    sort: RunSort = RunSort.newest, offset: int = 0,
                                    limit: int = 100) -> PaginatedRunMetadata:
    """Every run, standalone or from a set's or plan's execution, within the
    filters, sorted and paged; each with its test's and scope's names, its
    checks counted and its error."""
    filters = filters or RunFilters()
    where = RUNS.where(filters.chosen(), filters.q)
    found = (await session.execute(RUNS.page(
        select(TestRunModel.id, TestRunModel.status, TestRunModel.created_at,
               TestRunModel.test_id, TestRunModel.test_set_entry_id,
               TestRunModel.test_set_execution_id, TestRunModel.test_plan_execution_id,
               TestRunModel.batch_id, TestRunModel.batch_index, TestRunModel.results,
               TestRunModel.error, TestSetExecutionModel.test_set_id,
               TestPlanExecutionModel.test_plan_id, TEST_NAME.label("test_name"),
               TEST_ID.label("asked_test_id"),
               SCOPE_NAME.label("scope_name")),
        where, sort, offset, limit))).all()
    return PaginatedRunMetadata(total=await RUNS.count(session, where), offset=offset,
                                limit=limit, items=[_run_metadata_from_row(row) for row in found])


async def get_run_facets(session: AsyncSession, filters: RunFilters | None = None) -> RunFacets:
    """The runs within the filters, counted by status and by origin."""
    filters = filters or RunFilters()
    return RunFacets(**await RUNS.facet_counts(session, filters.chosen(), filters.q))


def _run_metadata_from_row(row: Row) -> RunMetadata:
    """A run's list item. Its origin follows from its own columns: `test_id`
    set means standalone, `test_set_execution_id` a set's run, otherwise a
    plan's; only that origin's ids are filled in."""
    common = dict(id=row.id, batch_id=row.batch_id, batch_index=row.batch_index,
                  status=row.status, created_at=row.created_at, test_name=row.test_name,
                  scope_name=row.scope_name, checks=count_checks(row.results), error=row.error,
                  test_case_id=TestCaseID(id=row.asked_test_id), test_set_entry_id=None,
                  test_set_execution_id=None,
                  test_plan_execution_id=None, test_set_id=None, test_plan_id=None)
    if row.test_id is not None:
        return RunMetadata(**common | dict(origin=RunOrigin.standalone))
    if row.test_set_execution_id is not None:
        return RunMetadata(**common | dict(
            origin=RunOrigin.test_set, test_set_entry_id=TestSetEntryID(id=row.test_set_entry_id),
            test_set_execution_id=TestSetExecutionID(id=row.test_set_execution_id),
            test_set_id=TestSetID(id=row.test_set_id)))
    return RunMetadata(**common | dict(
        origin=RunOrigin.test_plan, test_set_entry_id=TestSetEntryID(id=row.test_set_entry_id),
        test_plan_execution_id=TestPlanExecutionID(id=row.test_plan_execution_id),
        test_plan_id=TestPlanID(id=row.test_plan_id)))
