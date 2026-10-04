import uuid
from dataclasses import dataclass

from sqlalchemy import exists, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager
from sqlalchemy.sql.expression import select

from assay.models import (
    StatisticalBatchModel,
    TestPlanEntryModel,
    TestPlanModel,
    TestSetEntryModel,
    TestSetModel,
)
from assay.schemas import (
    PaginatedTestPlanLinks,
    PaginatedTestSetMetadataResponse,
    RunFilter,
    ScopeSort,
    TestPlanLink,
    TestSetFacets,
    TestSetMetadata,
    VerdictFilter,
)
from assay.services._listing import Facet, ListFilters, created_buckets, membership
from assay.services._scope_listing import set_listing
from assay.services._standing import refresh_batches
from assay.services.test_plans._common import _describe_test_plans
from assay.services.test_sets._common import _describe_test_sets, _find_test_set_or_404


def _in_test_plan(ids: list | None):
    return exists().where(TestPlanEntryModel.test_set_id == TestSetModel.id,
                          *([TestPlanEntryModel.test_plan_id.in_(ids)] if ids is not None else []))


TEST_SETS = set_listing(
    filters={
        "in_test_plan": lambda values: membership(values, _in_test_plan),
        "holds_test": lambda values: exists().where(
            TestSetEntryModel.test_set_id == TestSetModel.id,
            TestSetEntryModel.test_id.in_(values)),
    },
    facets={
        "in_test_plan": Facet(TestPlanEntryModel.test_plan_id, join=lambda statement: (
            statement.join(TestPlanEntryModel, TestPlanEntryModel.test_set_id == TestSetModel.id)),
            buckets={"any": _in_test_plan(None), "none": ~_in_test_plan(None)}),
        "holds_test": Facet(TestSetEntryModel.test_id, join=lambda statement: (
            statement.join(TestSetEntryModel, TestSetEntryModel.test_set_id == TestSetModel.id))),
    },
)


@dataclass(frozen=True)
class TestSetFilters(ListFilters):
    """The test sets list's filters, as the API received them."""
    latest_verdict: list[VerdictFilter] | None = None
    latest_run: list[RunFilter] | None = None
    in_test_plan: list[str] | None = None
    holds_test: list[uuid.UUID] | None = None

    def chosen(self) -> dict:
        return {"latest_verdict": self.latest_verdict, "latest_run": self.latest_run,
                "in_test_plan": self.in_test_plan, "holds_test": self.holds_test,
                "created": self.created()}


async def get_all_test_sets_metadata(session: AsyncSession,
                                     filters: TestSetFilters | None = None,
                                     sort: ScopeSort = ScopeSort.latest_run, offset: int = 0,
                                     limit: int = 100) -> PaginatedTestSetMetadataResponse:
    """The test sets within the filters, sorted and paged, each as it's read."""
    filters = filters or TestSetFilters()
    await refresh_batches(session, StatisticalBatchModel.test_set_id.is_not(None))
    where = TEST_SETS.where(filters.chosen(), filters.q)
    test_sets = list((await session.scalars(
        TEST_SETS.page(select(TestSetModel), where, sort, offset, limit))).all())
    return PaginatedTestSetMetadataResponse(
        total=await TEST_SETS.count(session, where), offset=offset, limit=limit,
        items=await _describe_test_sets(test_sets, session))


async def get_test_set_metadata_by_id(
        test_set_id: uuid.UUID,
        session: AsyncSession,
) -> TestSetMetadata:
    """Orchestrates single test set retrieval: validates ID and returns its metadata.

    Args:
        test_set_id: The UUID of the test set to retrieve.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The test set metadata (id, name, created_at, entry_count).

    Raises:
        HTTPException 404: No test set exists with the given ID.
    """
    test_set = await _find_test_set_or_404(test_set_id, session)

    (described,) = await _describe_test_sets([test_set], session)
    return described


async def get_test_plans_linking_set(test_set_id: uuid.UUID, session: AsyncSession,
                                     offset: int = 0, limit: int = 100) -> PaginatedTestPlanLinks:
    """The test plans linking a test set, by name, each as its list shows it,
    with the plan's entry that links the set.

    Raises:
        HTTPException 404: No test set exists with the given ID.
    """
    await _find_test_set_or_404(test_set_id, session)
    linking = TestPlanEntryModel.test_set_id == test_set_id
    total = await session.scalar(select(func.count(TestPlanEntryModel.id)).where(linking)) or 0
    entries = list((await session.scalars(
        select(TestPlanEntryModel).join(TestPlanEntryModel.test_plan).where(linking)
        .options(contains_eager(TestPlanEntryModel.test_plan))
        .order_by(func.lower(TestPlanModel.name), TestPlanModel.id)
        .offset(offset).limit(limit))).all())
    test_plans = await _describe_test_plans([entry.test_plan for entry in entries], session)
    return PaginatedTestPlanLinks(
        total=total, offset=offset, limit=limit,
        items=[TestPlanLink(test_plan=test_plan, entry_id=entry.id)
               for entry, test_plan in zip(entries, test_plans, strict=True)])


async def get_test_set_facets(session: AsyncSession, filters: TestSetFilters | None = None,
                              created_edges: list[str] | None = None) -> TestSetFacets:
    """The test sets within the filters, counted by each filter's values."""
    filters = filters or TestSetFilters()
    await refresh_batches(session, StatisticalBatchModel.test_set_id.is_not(None))
    extra = ({"created": Facet(buckets=created_buckets(TestSetModel.created_at, created_edges))}
             if created_edges else None)
    return TestSetFacets(**await TEST_SETS.facet_counts(session, filters.chosen(), filters.q,
                                                        extra))
