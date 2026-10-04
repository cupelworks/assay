import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, case, exists, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload
from sqlalchemy.sql.expression import select

from assay.models import (
    BatchStatus,
    DatasetRowModel,
    StatisticalBatchModel,
    TestModel,
    TestRunModel,
    TestSetEntryModel,
    TestSetModel,
    TestStatus,
    TestTypeAssignmentModel,
)
from assay.schemas import (
    EntryCopy,
    PaginatedTestCases,
    PaginatedTestSetHoldings,
    RunFilter,
    TestCaseRead,
    TestFacets,
    TestSetHolding,
    TestSort,
    VerdictFilter,
)
from assay.services._listing import (
    Facet,
    ListFilters,
    Listing,
    among,
    contains_text,
    created_between,
    created_buckets,
    membership,
)
from assay.services._standing import (
    entries_with_runs,
    latest_batch_sql,
    latest_test_run_sql,
    refresh_batches,
)
from assay.services.test_sets._common import _describe_test_sets
from assay.services.tests._common import (
    _copy_matches_test,
    _describe_tests,
    _find_test_by_id_or_404,
)


def _labels_or_types(q: str):
    return exists().where(TestTypeAssignmentModel.test_id == TestModel.id,
                          contains_text(q, TestTypeAssignmentModel.label,
                                        TestTypeAssignmentModel.test_type_name))


def _in_test_set(ids: list | None):
    linked = (TestSetEntryModel.test_set_id.in_(ids) if ids is not None
              else TestSetEntryModel.test_set_id.is_not(None))
    return exists().where(TestSetEntryModel.test_id == TestModel.id, linked)


_LATEST_VERDICT = latest_batch_sql(StatisticalBatchModel.test_id, TestModel.id,
                                   StatisticalBatchModel.status)
_LATEST_RUN = latest_test_run_sql(TestModel.id, TestRunModel.status)
_LATEST_RUN_AT = latest_test_run_sql(TestModel.id, TestRunModel.created_at)
_LATEST_BATCH_AT = latest_batch_sql(StatisticalBatchModel.test_id, TestModel.id,
                                    StatisticalBatchModel.created_at)
# the newest of its latest run and latest batch, else when it was made
_LATEST_ACTIVITY = case(
    (_LATEST_RUN_AT.is_(None), func.coalesce(_LATEST_BATCH_AT, TestModel.created_at)),
    (_LATEST_BATCH_AT.is_(None), _LATEST_RUN_AT),
    (_LATEST_RUN_AT > _LATEST_BATCH_AT, _LATEST_RUN_AT),
    else_=_LATEST_BATCH_AT)


def _of_its_row(column) -> ColumnElement:
    """`column` of the dataset row the outer test was made from; null without one."""
    return (select(column).where(DatasetRowModel.id == TestModel.dataset_row_id)
            .correlate(TestModel).scalar_subquery())


_ROW_DATASET, _ROW_NUMBER = _of_its_row(DatasetRowModel.dataset_id), _of_its_row(
    DatasetRowModel.position)

TESTS = Listing(
    key=TestModel.id,
    source=lambda statement: statement.select_from(TestModel),
    filters={
        "check_type": lambda values: exists().where(
            TestTypeAssignmentModel.test_id == TestModel.id,
            TestTypeAssignmentModel.test_type_name.in_(values)),
        "latest_verdict": lambda values: among(_LATEST_VERDICT, values, "none", BatchStatus),
        "latest_run": lambda values: among(_LATEST_RUN, values, "never", TestStatus),
        "has_recorded_answer": lambda values: (TestModel.model_output.is_not(None) if values[0]
                                               else TestModel.model_output.is_(None)),
        "in_test_set": lambda values: membership(values, _in_test_set),
        "from_dataset": lambda values: TestModel.dataset_row_id.in_(
            select(DatasetRowModel.id).where(DatasetRowModel.dataset_id.in_(values))),
        "created": lambda values: created_between(TestModel.created_at, *values),
    },
    search=lambda q: or_(contains_text(q, TestModel.name, TestModel.input), _labels_or_types(q)),
    sorts={TestSort.latest_activity: (_LATEST_ACTIVITY.desc(),),
           TestSort.name: (func.lower(TestModel.name),),
           TestSort.created: (TestModel.created_at.desc(),),
           TestSort.dataset_row: (_ROW_NUMBER.is_(None), _ROW_DATASET, _ROW_NUMBER,
                                  TestModel.created_at)},
    facets={
        "check_type": Facet(TestTypeAssignmentModel.test_type_name, join=lambda statement: (
            statement.join(TestTypeAssignmentModel,
                           TestTypeAssignmentModel.test_id == TestModel.id))),
        "latest_verdict": Facet(_LATEST_VERDICT, absent="none",
                                values=[value.value for value in VerdictFilter]),
        "latest_run": Facet(_LATEST_RUN, absent="never",
                            values=[value.value for value in RunFilter]),
        "has_recorded_answer": Facet(buckets={"true": TestModel.model_output.is_not(None),
                                              "false": TestModel.model_output.is_(None)}),
        "in_test_set": Facet(TestSetEntryModel.test_set_id, join=lambda statement: (
            statement.join(TestSetEntryModel, and_(TestSetEntryModel.test_id == TestModel.id,
                                                   TestSetEntryModel.test_set_id.is_not(None)))),
            buckets={"any": _in_test_set(None), "none": ~_in_test_set(None)}),
        "from_dataset": Facet(DatasetRowModel.dataset_id, join=lambda statement: (
            statement.join(DatasetRowModel, DatasetRowModel.id == TestModel.dataset_row_id))),
    },
)


@dataclass(frozen=True)
class TestFilters(ListFilters):
    """The tests list's filters, as the API received them."""
    check_type: list[str] | None = None
    latest_verdict: list[VerdictFilter] | None = None
    latest_run: list[RunFilter] | None = None
    has_recorded_answer: bool | None = None
    in_test_set: list[str] | None = None
    from_dataset: list[uuid.UUID] | None = None

    def chosen(self) -> dict:
        return {"check_type": self.check_type, "latest_verdict": self.latest_verdict,
                "latest_run": self.latest_run,
                "has_recorded_answer": (None if self.has_recorded_answer is None
                                        else [self.has_recorded_answer]),
                "in_test_set": self.in_test_set, "from_dataset": self.from_dataset,
                "created": self.created()}


async def get_all_created_tests(session: AsyncSession, filters: TestFilters | None = None,
                                sort: TestSort = TestSort.latest_activity, offset: int = 0,
                                limit: int = 100) -> PaginatedTestCases:
    """The tests within the filters, sorted and paged, each as it's read (its
    checks in label order, how it stands)."""
    filters = filters or TestFilters()
    await refresh_batches(session, StatisticalBatchModel.test_id.is_not(None))
    where = TESTS.where(filters.chosen(), filters.q)
    tests = list((await session.scalars(TESTS.page(
        select(TestModel).options(selectinload(TestModel.test_type_assignments)),
        where, sort, offset, limit))).all())
    return PaginatedTestCases(test_cases=await _describe_tests(tests, session), offset=offset,
                              limit=limit, total=await TESTS.count(session, where))


async def get_test_case_by_id(
        test_case_id: uuid.UUID,
        session: AsyncSession,
) -> TestCaseRead:
    """Fetch a single test case by ID and return it as a response schema.

    Args:
        test_case_id: UUID of the test case to retrieve.
        session: Active async database session.

    Returns:
        The matching test case with all fields and assigned test type names.

    Raises:
        HTTPException: 404 if no test case with the given ID exists.
    """
    test = await _find_test_by_id_or_404(test_case_id, session)
    
    (described,) = await _describe_tests([test], session)
    return described


async def get_test_sets_holding_test(test_id: uuid.UUID, session: AsyncSession,
                                     offset: int = 0, limit: int = 100
                                     ) -> PaginatedTestSetHoldings:
    """The test sets holding a copy of a test, by name, each with its copy: has
    it run, and does it still ask what the test asks; and how many copies
    were unlinked from their set.

    Raises:
        HTTPException 404: No test exists with the given ID.
    """
    test = await _find_test_by_id_or_404(test_id, session)
    linked = [TestSetEntryModel.test_id == test_id, TestSetEntryModel.test_set_id.is_not(None)]
    total = await session.scalar(select(func.count(TestSetEntryModel.id)).where(*linked)) or 0
    entries = list((await session.scalars(
        select(TestSetEntryModel).join(TestSetEntryModel.test_set).where(*linked)
        .options(contains_eager(TestSetEntryModel.test_set))
        .order_by(func.lower(TestSetModel.name), TestSetModel.id)
        .offset(offset).limit(limit))).all())
    test_sets = await _describe_test_sets([entry.test_set for entry in entries], session)
    with_runs = await entries_with_runs(session, [entry.id for entry in entries])
    unlinked = await session.scalar(select(func.count(TestSetEntryModel.id)).where(
        TestSetEntryModel.test_id == test_id, TestSetEntryModel.test_set_id.is_(None))) or 0
    return PaginatedTestSetHoldings(
        total=total, offset=offset, limit=limit, unlinked_copies=unlinked,
        items=[TestSetHolding(test_set=test_set, entry=EntryCopy(
            id=entry.id, has_runs=entry.id in with_runs,
            matches_test=_copy_matches_test(entry, test)))
            for entry, test_set in zip(entries, test_sets, strict=True)])


async def get_test_facets(session: AsyncSession, filters: TestFilters | None = None,
                          created_edges: list[str] | None = None) -> TestFacets:
    """The tests within the filters, counted by each filter's values, each
    within every other filter; the created range by the given edges."""
    filters = filters or TestFilters()
    await refresh_batches(session, StatisticalBatchModel.test_id.is_not(None))
    extra = ({"created": Facet(buckets=created_buckets(TestModel.created_at, created_edges))}
             if created_edges else None)
    return TestFacets(**await TESTS.facet_counts(session, filters.chosen(), filters.q, extra))
