# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""List comparisons, newest first, by batch, scope and outcome, and count them
by scope and outcome."""
import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import StatisticalComparisonModel
from assay.schemas import ComparisonFacets
from assay.schemas.statistics import ComparisonList, ComparisonOutcome
from assay.services._listing import Facet, Listing
from assay.services.statistics._comparisons import describe_many
from assay.services.statistics._lists import ScopeFilters, scope_facets, scope_filters

_NEWEST = "newest"
_MODEL = StatisticalComparisonModel

COMPARISONS = Listing(
    key=_MODEL.id,
    source=lambda statement: statement.select_from(_MODEL),
    filters={"batch_id": lambda ids: or_(_MODEL.batch_a_id.in_(ids), _MODEL.batch_b_id.in_(ids)),
             "outcome": lambda values: _MODEL.outcome.in_([value.value for value in values]),
             **scope_filters(_MODEL)},
    sorts={_NEWEST: (_MODEL.created_at.desc(),)},
    facets={"outcome": Facet(_MODEL.outcome, values=[value.value for value in ComparisonOutcome]),
            **scope_facets(_MODEL)},
)


@dataclass(frozen=True)
class ComparisonFilters(ScopeFilters):
    """The comparisons list's filters, as the API received them."""
    batch_id: uuid.UUID | None = None
    outcome: list[ComparisonOutcome] | None = None

    def chosen(self) -> dict:
        return {**super().chosen(), "outcome": self.outcome,
                "batch_id": [self.batch_id] if self.batch_id else None}


async def list_comparisons(session: AsyncSession, *, offset: int, limit: int,
                           filters: ComparisonFilters | None = None) -> ComparisonList:
    """The comparisons within the filters, newest first, paged."""
    where = COMPARISONS.where((filters or ComparisonFilters()).chosen())
    page = list((await session.scalars(
        COMPARISONS.page(select(_MODEL), where, _NEWEST, offset, limit))).all())
    items = await describe_many(page, session, with_result=False)
    return ComparisonList(items=items, total=await COMPARISONS.count(session, where),
                          offset=offset, limit=limit)


async def get_comparison_facets(session: AsyncSession,
                                filters: ComparisonFilters | None = None) -> ComparisonFacets:
    """The comparisons `list_comparisons` would list, counted by outcome and by
    scope: each facet within every other filter chosen, never its own."""
    chosen = (filters or ComparisonFilters()).chosen()
    return ComparisonFacets(**await COMPARISONS.facet_counts(session, chosen))
