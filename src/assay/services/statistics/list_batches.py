# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""List batches, newest first, by scope and status, and count them by both."""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import BatchStatus, StatisticalBatchModel
from assay.schemas import BatchFacets
from assay.schemas.statistics import BatchList, BatchStatusName
from assay.services._listing import Facet, Listing
from assay.services.statistics._batches import describe, refresh_in_progress, scope_names
from assay.services.statistics._lists import ScopeFilters, scope_facets, scope_filters

_NEWEST = "newest"

BATCHES = Listing(
    key=StatisticalBatchModel.id,
    source=lambda statement: statement.select_from(StatisticalBatchModel),
    filters={"status": lambda values: StatisticalBatchModel.status.in_(
                 [BatchStatus(value.value) for value in values]),
             **scope_filters(StatisticalBatchModel)},
    sorts={_NEWEST: (StatisticalBatchModel.created_at.desc(),)},
    facets={"status": Facet(StatisticalBatchModel.status,
                            values=[value.value for value in BatchStatusName]),
            **scope_facets(StatisticalBatchModel)},
)


@dataclass(frozen=True)
class BatchFilters(ScopeFilters):
    """The batches list's filters, as the API received them."""
    status: list[BatchStatusName] | None = None

    def chosen(self) -> dict:
        return {**super().chosen(), "status": self.status}


async def _refreshed(session: AsyncSession, filters: BatchFilters) -> dict:
    """The filters chosen, after the in-progress batches of the scopes asked
    for are brought up to date — one small count each — so a status filter or
    count sees the truth, and a batch that finished since its last read gets
    its result. Batches of other scopes are left for their own reads."""
    chosen = filters.chosen()
    await refresh_in_progress(session, *BATCHES.where(chosen, without="status"))
    return chosen


async def list_batches(session: AsyncSession, *, offset: int, limit: int,
                       filters: BatchFilters | None = None) -> BatchList:
    """The batches within the filters, newest first, paged."""
    chosen = await _refreshed(session, filters or BatchFilters())
    where = BATCHES.where(chosen)
    page = list((await session.scalars(
        BATCHES.page(select(StatisticalBatchModel), where, _NEWEST, offset, limit))).all())
    names = await scope_names(page, session)
    items = [await describe(batch, session, with_result=False, names=names) for batch in page]
    return BatchList(items=items, total=await BATCHES.count(session, where),
                     offset=offset, limit=limit)


async def get_batch_facets(session: AsyncSession,
                           filters: BatchFilters | None = None) -> BatchFacets:
    """The batches `list_batches` would list, counted by status and by scope:
    each facet within every other filter chosen, never its own."""
    chosen = await _refreshed(session, filters or BatchFilters())
    return BatchFacets(**await BATCHES.facet_counts(session, chosen))
