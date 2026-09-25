import uuid
from datetime import datetime

from sqlalchemy import select

from assay.models import TERMINAL_STATUSES, TestRunModel, TestStatus, TestTypesModel
from assay.schemas import TestTypeAssignment, TestTypeResult
from assay.worker import app, evaluators
from assay.worker.db import get_session


@app.task()
def execute_run(run_id: uuid.UUID):
    with get_session() as session:
        run = session.scalar(
            select(TestRunModel).where(TestRunModel.id == run_id)
        )
        if run is None or run.status in TERMINAL_STATUSES:
            # Nothing to do: the run was deleted, or this run_id was already
            # processed - a basic guard against double dispatch, not the
            # full idempotency answer dev_notes.md note 3 leaves open.
            return

        run.status = TestStatus.running
        session.commit()

        try:
            entry, resolved = _resolve_content(session, run)
        except Exception as exc:
            # Nothing could be attempted at all - the entry/assignments
            # themselves couldn't be read. This is exactly what NotRan
            # means (note 1): error carries the reason, results stays null.
            run.status = TestStatus.not_ran
            run.error = str(exc)
            run.executed_at = datetime.now().astimezone()
            session.commit()
            return

        # evaluators.evaluate() is expected to return a TestTypeResult - the
        # same schema the API reads results back as (schemas/runs.py), so
        # both ends are pinned to one shape instead of agreeing by
        # convention. One assignment's own failure becomes its own
        # TestTypeResult.detail, not the whole run going NotRan, so it's
        # caught per-assignment here rather than by the broader try/except
        # above.
        results: dict[str, TestTypeResult] = {}
        for assignment, category in resolved:
            try:
                results[assignment.name] = evaluators.evaluate(assignment, category, entry)
            except Exception as exc:
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


def _resolve_content(
        session, run: TestRunModel
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
    if run.test_id is not None:
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
        ).all()
    )
    return entry, [
        (
            TestTypeAssignment(name=raw["name"], config=raw.get("config")),
            categories[raw["name"]],
        )
        for raw in raw_assignments
    ]
