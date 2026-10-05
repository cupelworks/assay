# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Compare two finished batches of the same scope, check by check
(docs/version_1/statistics/dev_notes.md notes 18, 22). Computed at once and stored."""
import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import (
    IN_PROGRESS_BATCH_STATUSES,
    StandaloneRunModel,
    StatisticalBatchModel,
    StatisticalComparisonModel,
    TestRunModel,
)
from assay.schemas.statistics import ComparisonDetails, ComparisonRequest, StatisticalTestKind
from assay.services.statistics._batches import (
    find_batch_or_404,
    load_entries,
    load_types,
    refresh,
    scope_kind,
    scope_names,
)
from assay.services.statistics._comparisons import describe_many
from assay.services.statistics.catalogue import _invalid, load_entry, resolve_parameters
from assay.services.statistics.compare import compare, outcome

logger = logging.getLogger(__name__)

# what makes a standalone test's runs answer the same question
_CONTENT = (("input", "the input"), ("expected_output", "the expected output"),
            ("test_type_assignments", "the checks"))


async def create_comparison(request: ComparisonRequest,
                            session: AsyncSession) -> ComparisonDetails:
    """Raises:
        RequestValidationError: 422 for the same batch twice, an unknown
            statistical test or a batch test, parameters out of range,
            batches of different scopes, or a
            standalone test edited between the two (its input, expected
            output or checks).
        HTTPException: 404 for an unknown batch; 409 while either batch
            still has runs pending or running.
    """
    if request.batch_a == request.batch_b:
        raise _invalid([(("batch_b",), "Compare two different batches")])
    chosen = await load_entry(request.statistical_test, StatisticalTestKind.comparison, session)
    parameters = resolve_parameters(chosen, request.parameters)
    batch_a = await find_batch_or_404(request.batch_a, session)
    batch_b = await find_batch_or_404(request.batch_b, session)
    for batch in (batch_a, batch_b):
        await refresh(batch, session)

    running = [f"batch {side} is {batch.status.value}"
               for side, batch in (("A", batch_a), ("B", batch_b))
               if batch.status in IN_PROGRESS_BATCH_STATUSES]
    if running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only finished batches can be compared: {' and '.join(running)}")

    await _check_same_scope(batch_a, batch_b, session)
    if batch_a.test_id is not None:
        await _check_same_content(batch_a, batch_b, session)

    entries_a = await load_entries(batch_a, session)
    entries_b = await load_entries(batch_b, session)
    types = await load_types(entries_a + entries_b, session)
    result = compare(entries_a, entries_b, parameters, chosen.engine.id, types)
    comparison = StatisticalComparisonModel(
        id=uuid.uuid4(), batch_a_id=batch_a.id, batch_b_id=batch_b.id,
        test_id=batch_a.test_id, test_set_id=batch_a.test_set_id,
        test_plan_id=batch_a.test_plan_id, statistical_test=chosen.id,
        engine=chosen.engine.id.value,
        parameters=parameters, note=(request.note or "").strip() or None,
        result=result.model_dump(mode="json"), outcome=outcome(result.verdicts).value,
    )
    session.add(comparison)
    await session.commit()
    await session.refresh(comparison)  # answer with what a read will return
    logger.info(
        "Compared batch %s against batch %s: %s", batch_b.id, batch_a.id, result.verdicts,
        extra={"comparison_id": comparison.id, "batch_a_id": batch_a.id,
               "batch_b_id": batch_b.id, "verdicts": result.verdicts},
    )
    (described,) = await describe_many([comparison], session, with_result=True)
    return described


async def _check_same_scope(batch_a: StatisticalBatchModel, batch_b: StatisticalBatchModel,
                            session: AsyncSession) -> None:
    kind_a, id_a = scope_kind(batch_a)
    kind_b, id_b = scope_kind(batch_b)
    if (kind_a, id_a) == (kind_b, id_b):
        return
    names = await scope_names([batch_a, batch_b], session)
    words = {"test": "test", "test_set": "test set", "test_plan": "test plan"}
    raise _invalid([(("batch_b",),
                     f"Batch B ran {words[kind_b.value]} '{names.get(id_b, id_b)}', batch A "
                     f"{words[kind_a.value]} '{names.get(id_a, id_a)}': compare two batches "
                     "of the same scope")])


async def _first_copy(batch: StatisticalBatchModel,
                      session: AsyncSession) -> StandaloneRunModel | None:
    return await session.scalar(
        select(StandaloneRunModel)
        .join(TestRunModel, TestRunModel.id == StandaloneRunModel.id)
        .where(TestRunModel.batch_id == batch.id)
        .limit(1))


async def _check_same_content(batch_a: StatisticalBatchModel, batch_b: StatisticalBatchModel,
                              session: AsyncSession) -> None:
    """A standalone test can be edited between two batches; its runs then
    answered a different question, and comparing them would mislead. A
    different recorded answer is allowed: that's the change being measured."""
    copy_a, copy_b = await _first_copy(batch_a, session), await _first_copy(batch_b, session)
    if copy_a is None or copy_b is None:
        return
    changed = [label for column, label in _CONTENT
               if getattr(copy_a, column) != getattr(copy_b, column)]
    if changed:
        raise _invalid([(("batch_b",),
                         f"The test was edited between the two batches ({', '.join(changed)} "
                         "changed): their runs answered different questions. Compare two "
                         "batches of the same content")])
