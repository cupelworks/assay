"""Run a scope N times as one batch (docs/statistics/dev_notes.md notes 2, 3,
21): N standalone runs of a test, or N live executions of a set or plan, all
created in one transaction with the batch, then dispatched to the workers as
any runs are."""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from assay import sequential
from assay.models import BatchStatus, StatisticalBatchModel
from assay.schemas.statistics import (
    BatchDetails,
    BatchRequest,
    StatisticalEngine,
    StatisticalTestKind,
)
from assay.services.runs._common import _dispatch_runs
from assay.services.runs.create_new_run import (
    _new_standalone_run,
    _new_test_plan_execution,
    _new_test_set_execution,
)
from assay.services.statistics._batches import describe
from assay.services.statistics._scope import resolve_scope
from assay.services.statistics.catalogue import SEQUENTIAL, load_entry, resolve_parameters
from assay.services.statistics.estimate import build_estimate, resolve_overrides
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
        RequestValidationError: 422 for an unknown statistical test or a
            comparison test, parameters out of range, times below the floor or
            above the limits, or no applicable check.
    """
    chosen = await load_entry(request.statistical_test, StatisticalTestKind.batch, session)
    parameters = resolve_parameters(chosen, request.parameters)
    scope = await resolve_scope(request, session)
    targets, leave_out = resolve_overrides(request, chosen, scope)
    estimate = await build_estimate(chosen, parameters, request.times, scope, session,
                                    targets=targets, leave_out=leave_out)
    times = estimate.times
    overrides = None
    if targets or leave_out:
        overrides = {
            "targets": [{"entry_id": str(entry) if entry else None, "label": label,
                         "target": target} for (entry, label), target in targets.items()],
            "leave_out": [{"entry_id": str(entry) if entry else None, "label": label}
                          for entry, label in sorted(leave_out, key=str)],
        }
    # what each entry's runs skip: its left-out checks
    skips: dict = {}
    for entry_id, label in leave_out:
        skips.setdefault(entry_id, []).append(label)

    plan = {"floor": estimate.floor,
            "calls_per_time": {"application": estimate.calls.application.per_time,
                               "judge": estimate.calls.judge.per_time}}
    # until there's an answer: `times` is the most it may run; only the first
    # wave is created now, the worker releases the next after each one
    waves = None
    if chosen.engine.id == StatisticalEngine.sequential_t:
        waves = sequential.mean_wave_plan(times, chosen.settings["floor"],
                                          parameters["confidence"])
        plan["waves"] = waves.to_json()
    elif chosen.engine.id in SEQUENTIAL:
        waves = sequential.wave_plan(times, parameters["target"], parameters["confidence"])
        plan["waves"] = waves.to_json()
    batch = StatisticalBatchModel(
        id=uuid.uuid4(), test_id=request.test_id, test_set_id=request.test_set_id,
        test_plan_id=request.test_plan_id, statistical_test=chosen.id,
        engine=chosen.engine.id.value,
        parameters=parameters, times_requested=times, runs_per_time=scope.runs_per_time,
        plan=plan, waves_released=1 if waves else None,
        note=(request.note or "").strip() or None, status=BatchStatus.pending,
        overrides=overrides,
    )
    session.add(batch)
    await session.flush()

    runs, executions = [], []
    if request.test_id:
        (test,) = await _find_all_tests_with_details_or_404([request.test_id], session)
        runs = [_new_standalone_run(test, batch_id=batch.id, batch_index=index)
                for index in range(1, (waves.first if waves else times) + 1)]
    else:
        entry_ids = [entry.entry_id for entry in scope.entries]
        build = (_new_test_set_execution if request.test_set_id
                 else _new_test_plan_execution)
        for index in range(1, (waves.first if waves else times) + 1):
            execution, time_runs = build(scope.scope.id, entry_ids, batch_id=batch.id,
                                         batch_index=index)
            executions.append(execution)
            runs.extend(time_runs)
    for run in runs:
        run.skip_labels = sorted(skips.get(run.test_set_entry_id, [])) or None
    session.add_all(executions)
    session.add_all(runs)
    await session.commit()

    logger.info(
        "Created batch %s of %s %s: %s (%s), %d times, %d runs, %d application and %d judge "
        "calls",
        batch.id, scope.scope.kind.value, scope.scope.id, chosen.id, chosen.engine.id.value,
        times,
        len(runs), estimate.calls.application.total, estimate.calls.judge.total,
        extra={"batch_id": batch.id, "scope_kind": scope.scope.kind.value,
               "scope_id": scope.scope.id, "statistical_test": chosen.id,
               "engine": chosen.engine.id.value, "times": times,
               "run_count": len(runs),
               "application_calls": estimate.calls.application.total,
               "judge_calls": estimate.calls.judge.total},
    )
    _dispatch_runs([run.id for run in runs])
    await session.refresh(batch)  # answer with what a read will return
    return await describe(batch, session)
