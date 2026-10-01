"""What the comparison endpoints share: finding one, and the response."""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import StatisticalBatchModel, StatisticalComparisonModel
from assay.schemas.statistics import (
    BatchStatusName,
    ComparedBatch,
    ComparisonDetails,
    ComparisonResult,
    ComparisonSummary,
    Scope,
    StatisticalTestName,
)
from assay.services.statistics._batches import scope_kind, scope_names


async def find_comparison_or_404(comparison_id: uuid.UUID,
                                 session: AsyncSession) -> StatisticalComparisonModel:
    comparison = await session.get(StatisticalComparisonModel, comparison_id)
    if comparison is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Statistical comparison with ID {comparison_id} not found")
    return comparison


def compared(batch: StatisticalBatchModel) -> ComparedBatch:
    return ComparedBatch(id=batch.id, note=batch.note,
                         status=BatchStatusName(batch.status.value),
                         statistical_test=StatisticalTestName(batch.statistical_test),
                         times_requested=batch.times_requested, created_at=batch.created_at)


async def describe_many(comparisons: list[StatisticalComparisonModel], session: AsyncSession,
                        *, with_result: bool, series: bool = True
                        ) -> list[ComparisonSummary | ComparisonDetails]:
    batch_ids = {c.batch_a_id for c in comparisons} | {c.batch_b_id for c in comparisons}
    batches = {b.id: b for b in (await session.scalars(
        select(StatisticalBatchModel).where(StatisticalBatchModel.id.in_(batch_ids)))).all()}
    names = await scope_names([batches[c.batch_a_id] for c in comparisons], session)
    described = []
    for comparison in comparisons:
        batch_a = batches[comparison.batch_a_id]
        kind, scope_id = scope_kind(batch_a)
        fields = dict(
            id=comparison.id,
            scope=Scope(kind=kind, id=scope_id, name=names.get(scope_id, "")),
            statistical_test=StatisticalTestName(comparison.statistical_test),
            parameters=comparison.parameters, note=comparison.note,
            batch_a=compared(batch_a), batch_b=compared(batches[comparison.batch_b_id]),
            summary=comparison.result["summary"], created_at=comparison.created_at,
        )
        if not with_result:
            described.append(ComparisonSummary(**fields))
            continue
        result = ComparisonResult.model_validate(comparison.result)
        if not series:
            for entry in result.entries:
                for check in entry.checks:
                    check.a.series = check.b.series = None
        described.append(ComparisonDetails(**fields, result=result))
    return described
