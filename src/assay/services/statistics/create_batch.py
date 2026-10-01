"""Run a scope N times as one batch (docs/statistics/dev_notes.md notes 2, 3,
21): N standalone runs of a test, or N live executions of a set or plan, all
created in one transaction with the batch, then dispatched to the workers as
any runs are."""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import BatchStatus, StatisticalBatchModel
from assay.schemas.statistics import BatchDetails, BatchRequest, StatisticalTestKind
from assay.services.runs._common import _dispatch_runs
from assay.services.runs.create_new_run import (
    _new_standalone_run,
    _new_test_plan_execution,
    _new_test_set_execution,
)
from assay.services.statistics._batches import describe
from assay.services.statistics._scope import resolve_scope
from assay.services.statistics.catalogue import resolve_parameters
from assay.services.statistics.estimate import build_estimate
from assay.services.tests._common import _find_all_tests_with_details_or_404

logger = logging.getLogger(__name__)


async def create_batch(request: BatchRequest, session: AsyncSession) -> BatchDetails:
    """Create a batch and its runs, and dispatch every run.

    The guards are the estimate's — the same request to POST
    /statistics/estimate shows exactly what this creates — which include the
    run-creation guards of an ordinary run of the scope.

    Raises:
        HTTPException: 404 for an unknown test, set or plan; 409 for an empty
            set or plan, or an entry with no checks.
        RequestValidationError: 422 for a comparison test, parameters out of
            range, times below the floor or above the limits, or no applicable
            check.
    """
    name = request.statistical_test
    parameters = resolve_parameters(name, request.parameters, StatisticalTestKind.batch)
    scope = await resolve_scope(request, session)
    estimate = await build_estimate(name, parameters, request.times, scope, session)
    times = estimate.times

    batch = StatisticalBatchModel(
        id=uuid.uuid4(), test_id=request.test_id, test_set_id=request.test_set_id,
        test_plan_id=request.test_plan_id, statistical_test=name.value,
        parameters=parameters, times_requested=times, runs_per_time=scope.runs_per_time,
        plan={"floor": estimate.floor,
              "calls_per_time": {"application": estimate.calls.application.per_time,
                                 "judge": estimate.calls.judge.per_time}},
        note=(request.note or "").strip() or None, status=BatchStatus.pending,
    )
    session.add(batch)
    await session.flush()

    runs, executions = [], []
    if request.test_id:
        (test,) = await _find_all_tests_with_details_or_404([request.test_id], session)
        runs = [_new_standalone_run(test, batch_id=batch.id, batch_index=index)
                for index in range(1, times + 1)]
    else:
        entry_ids = [entry.entry_id for entry in scope.entries]
        build = (_new_test_set_execution if request.test_set_id
                 else _new_test_plan_execution)
        for index in range(1, times + 1):
            execution, time_runs = build(scope.scope.id, entry_ids, batch_id=batch.id,
                                         batch_index=index)
            executions.append(execution)
            runs.extend(time_runs)
    session.add_all(executions)
    session.add_all(runs)
    await session.commit()

    logger.info(
        "Created batch %s of %s %s: %s, %d times, %d runs, %d application and %d judge calls",
        batch.id, scope.scope.kind.value, scope.scope.id, name.value, times, len(runs),
        estimate.calls.application.total, estimate.calls.judge.total,
        extra={"batch_id": batch.id, "scope_kind": scope.scope.kind.value,
               "scope_id": scope.scope.id, "statistical_test": name.value, "times": times,
               "run_count": len(runs),
               "application_calls": estimate.calls.application.total,
               "judge_calls": estimate.calls.judge.total},
    )
    _dispatch_runs([run.id for run in runs])
    return await describe(batch, session)
