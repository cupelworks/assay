import uuid
from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import StatisticalBatchModel, TestPlanEntryModel, TestPlanModel
from assay.schemas import (
    PaginatedTestPlanMetadataResponse,
    RunFilter,
    ScopeSort,
    TestPlanFacets,
    TestPlanMetadata,
    VerdictFilter,
)
from assay.services._listing import Facet, ListFilters, created_buckets
from assay.services._scope_listing import plan_listing
from assay.services._standing import refresh_batches
from assay.services.test_plans._common import _describe_test_plans, _find_test_plan_by_id_or_404

TEST_PLANS = plan_listing(
    filters={"holds_test_set": lambda values: exists().where(
        TestPlanEntryModel.test_plan_id == TestPlanModel.id,
        TestPlanEntryModel.test_set_id.in_(values))},
    facets={"holds_test_set": Facet(TestPlanEntryModel.test_set_id, join=lambda statement: (
        statement.join(TestPlanEntryModel, TestPlanEntryModel.test_plan_id == TestPlanModel.id)))},
)


@dataclass(frozen=True)
class TestPlanFilters(ListFilters):
    """The test plans list's filters, as the API received them."""
    latest_verdict: list[VerdictFilter] | None = None
    latest_run: list[RunFilter] | None = None
    holds_test_set: list[uuid.UUID] | None = None

    def chosen(self) -> dict:
        return {"latest_verdict": self.latest_verdict, "latest_run": self.latest_run,
                "holds_test_set": self.holds_test_set, "created": self.created()}


async def get_all_test_plans_metadata(session: AsyncSession,
                                      filters: TestPlanFilters | None = None,
                                      sort: ScopeSort = ScopeSort.latest_run, offset: int = 0,
                                      limit: int = 100) -> PaginatedTestPlanMetadataResponse:
    """The test plans within the filters, sorted and paged, each as it's read."""
    filters = filters or TestPlanFilters()
    await refresh_batches(session, StatisticalBatchModel.test_plan_id.is_not(None))
    where = TEST_PLANS.where(filters.chosen(), filters.q)
    test_plans = list((await session.scalars(
        TEST_PLANS.page(select(TestPlanModel), where, sort, offset, limit))).all())
    return PaginatedTestPlanMetadataResponse(
        total=await TEST_PLANS.count(session, where), offset=offset, limit=limit,
        items=await _describe_test_plans(test_plans, session))


async def get_test_plan_metadata_by_id(
        test_plan_id: uuid.UUID,
        session: AsyncSession,
) -> TestPlanMetadata:
    """Orchestrates single test plan retrieval: validates ID and returns its metadata.

    Args:
        test_plan_id: The UUID of the test plan to retrieve.
        session: Async SQLAlchemy session injected by FastAPI.

    Returns:
        The test plan metadata (id, name, created_at, linked_set_count).

    Raises:
        HTTPException 404: No test plan exists with the given ID.
    """
    test_plan_model = await _find_test_plan_by_id_or_404(test_plan_id, session)

    (described,) = await _describe_test_plans([test_plan_model], session)
    return described


async def get_test_plan_facets(session: AsyncSession, filters: TestPlanFilters | None = None,
                               created_edges: list[str] | None = None) -> TestPlanFacets:
    """The test plans within the filters, counted by each filter's values."""
    filters = filters or TestPlanFilters()
    await refresh_batches(session, StatisticalBatchModel.test_plan_id.is_not(None))
    extra = ({"created": Facet(buckets=created_buckets(TestPlanModel.created_at, created_edges))}
             if created_edges else None)
    return TestPlanFacets(**await TEST_PLANS.facet_counts(session, filters.chosen(), filters.q,
                                                          extra))
