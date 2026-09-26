import logging
import time
import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from assay.models import TestRunModel, TestStatus, TestTypesModel
from assay.schemas import TestTypeAssignment, TestTypeResult
from assay.worker import evaluators

logger = logging.getLogger(__name__)


def execute_run(run_id: uuid.UUID, session: Session) -> None:
    """Orchestrates one run's execution: claim it, fetch, resolve content,
    evaluate every assigned test type, roll up the outcome, write it back.

    session is a plain parameter here, not acquired internally, so this can
    be unit tested with a fake session the same way every service function
    in assay/services/ already is — tasks/execute_run.py is the thin,
    untested Celery wrapper that acquires a real one and delegates here.

    Args:
        run_id: UUID of the TestRunModel to execute.
        session: Active sync SQLAlchemy session (assay.worker.db).
    """
    claim = session.execute(
        update(TestRunModel)
        .where(TestRunModel.id == run_id, TestRunModel.status == TestStatus.pending)
        .values(status=TestStatus.running)
    )
    session.commit()
    if claim.rowcount == 0:
        # Nothing matched: run_id doesn't exist, or its status was already
        # something other than pending (already claimed by another worker,
        # already running, or already terminal) - this call has nothing to
        # do either way. Expected for a duplicate delivery, still worth a
        # line: it's the only visible trace of a reconciliation re-publish.
        logger.info("Run %s not claimed: missing, already running or already terminal", run_id)
        return

    run = session.scalar(select(TestRunModel).where(TestRunModel.id == run_id))
    started = time.perf_counter()

    try:
        entry, resolved = _resolve_content(run, session)
    except Exception as exc:
        # Nothing could be attempted at all - the entry/assignments
        # themselves couldn't be read. This is exactly what NotRan means:
        # error carries the reason, results stays null.
        logger.exception("Run %s could not be executed: %s", run_id, exc)
        run.status = TestStatus.not_ran
        run.error = str(exc)
        run.executed_at = datetime.now().astimezone()
        session.commit()
        return

    origin = "standalone" if run.test_id is not None else "test set entry"
    logger.info(
        "Executing run %s (%s, %d test types)", run_id, origin, len(resolved),
        extra={"origin": origin, "test_type_count": len(resolved)},
    )

    # evaluators.evaluate() is expected to return a TestTypeResult - the
    # same schema the API reads results back as (schemas/runs.py), so both
    # ends are pinned to one shape instead of agreeing by convention. One
    # assignment's own failure becomes its own TestTypeResult.detail, not
    # the whole run going NotRan, so it's caught per-assignment here rather
    # than by the broader try/except above.
    results: dict[str, TestTypeResult] = {}
    for assignment, category in resolved:
        try:
            results[assignment.name] = evaluators.evaluate(assignment, category, entry)
        except Exception as exc:
            logger.warning(
                "Evaluator %s (%s) failed for run %s: %s",
                assignment.name, category, run_id, exc,
                exc_info=True, extra={"test_type": assignment.name, "category": category},
            )
            results[assignment.name] = TestTypeResult(passed=False, score=None, detail=str(exc))

    passed_flags = [result.passed for result in results.values()]
    if all(passed_flags):
        status = TestStatus.green
    elif not any(passed_flags):
        status = TestStatus.red
    else:
        status = TestStatus.amber

    run.results = {name: result.model_dump() for name, result in results.items()}
    run.status = status
    run.executed_at = datetime.now().astimezone()
    session.commit()

    passed = sum(passed_flags)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.info(
        "Run %s finished: %s (%d/%d passed) in %.1f ms",
        run_id, status.value, passed, len(passed_flags), duration_ms,
        extra={
            "status": status.value,
            "passed": passed,
            "test_type_count": len(passed_flags),
            "duration_ms": duration_ms,
        },
    )


def _resolve_content(
        run: TestRunModel | None, session: Session
) -> tuple[object, list[tuple[TestTypeAssignment, str]]]:
    """Two cases, not three. Standalone reads the live TestModel plus a
    join to the test_type_assignments table for category; test-set- and
    test-plan-triggered runs both read the same frozen TestSetEntryModel,
    whose test_type_assignments are already embedded as JSON on the row,
    needing only a lookup against the TestTypesModel catalogue to resolve
    each one's category.

    category rides alongside each TestTypeAssignment rather than being a
    field on it — it's only ever used to pick which evaluator function to
    call (evaluators.dispatch); once inside deterministic.evaluate() (say),
    the function already knows its own category, so it isn't data the
    assignment itself needs to carry.
    """
    if run is not None and run.test_id is not None:
        entry = run.test
        return entry, [
            (
                TestTypeAssignment(name=assignment.test_type_name, config=assignment.config),
                assignment.test_type.category,
            )
            for assignment in entry.test_type_assignments
        ]

    entry = run.test_set_entry
    raw_assignments = entry.test_type_assignments
    categories = dict(
        session.execute(
            select(TestTypesModel.name, TestTypesModel.category)
            .where(TestTypesModel.name.in_([raw["name"] for raw in raw_assignments]))
        ).tuples().all()
    )
    return entry, [
        (
            TestTypeAssignment(name=raw.get("name"), config=raw.get("config")),
            categories[raw.get("name")],
        )
        for raw in raw_assignments
    ]
