import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from assay.assignment_labels import labelled
from assay.judge_settings import resolve_judge_settings
from assay.messages import sentence
from assay.models import (
    OutputSource,
    SettingsModel,
    SettingsSection,
    TestRunModel,
    TestStatus,
    TestTypesModel,
)
from assay.schemas import JudgeSettings, TestTypeAssignment, TestTypeResult
from assay.schemas.settings import describe_validation_error
from assay.target_settings import resolve_target_settings
from assay.worker import evaluators, target

logger = logging.getLogger(__name__)

JUDGE_ENGINE = "llm_judge"


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
        # Results are keyed by label. Every copy saved since labels exist
        # carries them; labelled() gives any other one the labels the API
        # would have, so two checks of one type never share a key.
        resolved = list(zip(labelled([assignment for assignment, _ in resolved]),
                            [row for _, row in resolved], strict=True))
    except Exception as exc:
        # Nothing could be attempted at all - the entry/assignments
        # themselves couldn't be read. This is exactly what NotRan means:
        # error carries the reason, results stays null.
        logger.exception("Run %s could not be executed: %s", run_id, exc)
        run.status = TestStatus.not_ran
        run.error = sentence(str(exc))
        run.executed_at = datetime.now().astimezone()
        session.commit()
        return

    origin = "standalone" if run.test_id is not None else "test set entry"
    logger.info(
        "Executing run %s (%s, %d test types)", run_id, origin, len(resolved),
        extra={"origin": origin, "test_type_count": len(resolved)},
    )

    # The answer every engine scores: the copy's recorded model_output when
    # it has one, otherwise the application under test is asked now, with the
    # settings in effect at this moment - saved from the UI, else the
    # environment - read per run so a change applies without restarting the
    # worker. A failed call, or saved settings that no longer validate, means
    # nothing can be evaluated - NotRan with the reason, evaluated_output
    # left null. An empty answer is not a failed call: the application
    # answered with nothing (a refusal, a cut-off reply), and it's scored
    # like any other answer, so it counts in the results.
    settings_source = None
    reply = None
    if entry.model_output is not None:
        answer, output_source = entry.model_output, OutputSource.recorded
    else:
        try:
            target_settings = resolve_target_settings(
                session.get(SettingsModel, SettingsSection.target)
            )
            settings_source = target_settings.source
            response = target.get_answer(entry.input, target_settings)
            answer, reply = response.answer, response.reply
        except (target.TargetError, ValidationError) as exc:
            # target.py already logged a failing call itself
            reason = (
                f"The saved application settings are invalid: {describe_validation_error(exc)}"
                if isinstance(exc, ValidationError) else sentence(str(exc))
            )
            logger.error("Run %s could not be executed: %s", run_id, reason)
            run.status = TestStatus.not_ran
            run.error = reason
            run.executed_at = datetime.now().astimezone()
            session.commit()
            return
        output_source = OutputSource.application
        if response.empty:
            logger.warning(
                "Run %s: the application's answer is empty (%s); scoring it as an empty answer",
                run_id, response.empty,
            )

    # evaluators.evaluate() is expected to return a TestTypeResult - the
    # same schema the API reads results back as (schemas/runs.py), so both
    # ends are pinned to one shape instead of agreeing by convention. One
    # assignment's own failure becomes its own TestTypeResult.detail, not
    # the whole run going NotRan, so it's caught per-assignment here rather
    # than by the broader try/except above. A failed result is still stamped
    # with the engine the row named (None only if the type isn't in the
    # catalogue at all), so it records what *would* have scored it.
    judge, judge_problem = _judge_settings(resolved, session)
    results: dict[str, TestTypeResult] = {}
    for assignment, catalogue_row in resolved:
        engine = catalogue_row.engine if catalogue_row is not None else None
        try:
            if engine == JUDGE_ENGINE and judge_problem:
                raise ValueError(judge_problem)
            results[assignment.label] = evaluators.evaluate(
                assignment, catalogue_row, entry,
                _answer_for(assignment.answer_path, answer, reply, output_source),
                judge=judge,
            )
        except Exception as exc:
            logger.warning(
                "Check %s (%s, engine %s) failed for run %s: %s",
                assignment.label, assignment.name, engine, run_id, exc,
                exc_info=True, extra={"label": assignment.label, "test_type": assignment.name,
                                      "engine": engine},
            )
            results[assignment.label] = TestTypeResult(
                passed=False, score=None, detail=sentence(str(exc)), engine=engine,
                engine_settings=catalogue_row.engine_settings if catalogue_row else None,
                answer_path=assignment.answer_path, test_type=assignment.name,
                errored=True,
            )

    passed_flags = [result.passed for result in results.values()]
    if all(passed_flags):
        status = TestStatus.green
    elif not any(passed_flags):
        status = TestStatus.red
    else:
        status = TestStatus.amber

    run.results = {name: result.model_dump() for name, result in results.items()}
    run.evaluated_output = answer
    run.output_source = output_source
    run.application_reply = reply
    run.status = status
    run.executed_at = datetime.now().astimezone()
    session.commit()

    passed = sum(passed_flags)
    duration_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.info(
        "Run %s finished: %s (%d/%d passed, %s answer) in %.1f ms",
        run_id, status.value, passed, len(passed_flags), output_source.value, duration_ms,
        extra={
            "status": status.value,
            "passed": passed,
            "test_type_count": len(passed_flags),
            "output_source": output_source.value,
            "settings_source": settings_source.value if settings_source else None,
            "duration_ms": duration_ms,
        },
    )


def _judge_settings(
        resolved: list[tuple[TestTypeAssignment, TestTypesModel | None]], session: Session,
) -> tuple[JudgeSettings | None, str | None]:
    """The judge settings in effect, read once for a run that has a judge
    check — saved from the UI, else the environment, like the application's
    — or why they can't be used. Saved settings that no longer validate fail
    the run's judge checks with the reason; its other checks are scored."""
    if not any(row is not None and row.engine == JUDGE_ENGINE for _, row in resolved):
        return None, None
    try:
        return resolve_judge_settings(session.get(SettingsModel, SettingsSection.judge)), None
    except ValidationError as exc:
        return None, f"The saved judge settings are invalid: {describe_validation_error(exc)}"


def _answer_for(path: str | None, answer: str, reply: Any,
                output_source: OutputSource) -> str:
    """The text one check scores: the run's answer, or — when its assignment
    names an answer_path — that part of the application's whole reply, or
    of the recorded answer parsed as JSON, read by the same rules as the
    output path (a string as it is, a structured value as JSON text, null
    or blank as "").

    Raises:
        ValueError: nothing at the path, an invalid path, or a recorded
            answer that isn't JSON — that check's failure, with the reason.
    """
    if not path:
        return answer
    if output_source == OutputSource.application:
        source, where = reply, "the application's reply"
    else:
        try:
            source = json.loads(answer)
        except json.JSONDecodeError:
            raise ValueError(
                f"This check reads {path}, but the recorded answer isn't JSON"
            ) from None
        where = "the recorded answer"
    found = target.read_answer(source, path)
    if found is None:
        raise ValueError(f"Nothing found at {path} in {where}")
    return found[0]


def _resolve_content(
        run: TestRunModel | None, session: Session
) -> tuple[object, list[tuple[TestTypeAssignment, TestTypesModel | None]]]:
    """Every run evaluates a frozen copy of its test: a standalone run its
    own StandaloneRunModel, a test-set- or test-plan-triggered run the
    TestSetEntryModel it was created for. Both carry the same fields, with
    the assigned types embedded as the same JSON list, so there's one path:
    pick the copy, then fetch each assigned type's catalogue row in one
    query.

    The catalogue row rides alongside each TestTypeAssignment rather than
    being merged into it: the assignment is the frozen per-run choice (which
    type, with which config), the row is the catalogue's current definition
    of how that type is scored (engine, settings, comparison) — two different
    things, read from two different places, and the registry is what
    combines them. A type whose name isn't in the catalogue gets None here,
    and fails as that one check when evaluated, not as the whole run. A
    type assigned twice is two assignments with their own labels, and the
    run's results are keyed by label.
    """
    entry = run.standalone_run if run.test_id is not None else run.test_set_entry
    raw_assignments = entry.test_type_assignments
    rows_by_name = {
        row.name: row
        for row in session.scalars(
            select(TestTypesModel)
            .where(TestTypesModel.name.in_([raw["name"] for raw in raw_assignments]))
        )
    }
    return entry, [
        (
            TestTypeAssignment(name=raw.get("name"), label=raw.get("label"),
                               config=raw.get("config"), answer_path=raw.get("answer_path")),
            rows_by_name.get(raw.get("name")),
        )
        for raw in raw_assignments
    ]
