import logging
import uuid
from unittest.mock import MagicMock, patch

from assay.models import (
    Comparison,
    OutputSource,
    StandaloneRunModel,
    TestModel,
    TestRunModel,
    TestSetEntryModel,
    TestStatus,
    TestTypesModel,
)
from assay.schemas import TestTypeAssignment, TestTypeResult
from assay.worker.services.execute_run import _resolve_content, execute_run
from assay.worker.target import TargetError, TargetResponse

_PATCH_RESOLVE_CONTENT = "assay.worker.services.execute_run._resolve_content"
_PATCH_EVALUATE = "assay.worker.evaluators.evaluate"
_PATCH_GET_ANSWER = "assay.worker.services.execute_run.target.get_answer"

EXACT_MATCH = TestTypesModel(
    name="Exact Match", engine="exact_match",
    engine_settings={"trim": True, "case_sensitive": True}, comparison=None,
)
TOXICITY = TestTypesModel(
    name="Toxicity", engine="llm_judge", engine_settings={"default_rubric": "..."},
    comparison=None,
)
ROUGE = TestTypesModel(
    name="ROUGE", engine="rouge", engine_settings={"variant": "rougeL"},
    comparison=Comparison.gte,
)


def _recorded_entry(model_output="Go to Settings"):
    return TestSetEntryModel(input="How do I reset?", expected_output="Go to Settings",
                             model_output=model_output)


def _mock_successful_claim(session: MagicMock, run: TestRunModel) -> None:
    """Simulates the atomic claim UPDATE matching exactly one row, and the
    follow-up fetch returning that same run.
    """
    session.execute.return_value.rowcount = 1
    session.scalar.return_value = run


def _stamped(passed: bool, row: TestTypesModel, score=None, detail=None) -> dict:
    """What a result looks like once the registry has stamped the engine on it."""
    return {
        "passed": passed, "score": score, "detail": detail,
        "engine": row.engine, "engine_settings": row.engine_settings,
    }


def _fake_evaluate(passed_for: set[str]):
    def fake(assignment, catalogue_row, entry, answer):
        return TestTypeResult(
            passed=assignment.name in passed_for, score=None, detail=None,
            engine=catalogue_row.engine, engine_settings=catalogue_row.engine_settings,
        )
    return fake


# --- execute_run() ---


def test_claim_not_matching_any_row_is_a_no_op():
    session = MagicMock()
    session.execute.return_value.rowcount = 0

    with patch(_PATCH_EVALUATE) as mock_evaluate:
        execute_run(uuid.uuid4(), session)

    # covers run_id not existing, and every non-pending status (running,
    # already claimed by someone else, or already terminal) uniformly - the
    # claim's WHERE clause is what tells them apart, not Python here
    mock_evaluate.assert_not_called()
    session.scalar.assert_not_called()


def test_content_resolution_failure_marks_not_ran():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)

    with patch(_PATCH_RESOLVE_CONTENT, side_effect=RuntimeError("entry vanished")):
        execute_run(run_id, session)

    assert run.status == TestStatus.not_ran
    assert run.error == "entry vanished"
    assert run.results is None
    assert run.executed_at is not None


def test_every_type_passing_rolls_up_to_green():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)
    resolved = (
        _recorded_entry(),
        [(TestTypeAssignment(name="Exact Match"), EXACT_MATCH),
         (TestTypeAssignment(name="Toxicity"), TOXICITY)],
    )

    with patch(_PATCH_RESOLVE_CONTENT, return_value=resolved), \
            patch(_PATCH_EVALUATE, side_effect=_fake_evaluate({"Exact Match", "Toxicity"})):
        execute_run(run_id, session)

    assert run.status == TestStatus.green
    assert run.results == {
        "Exact Match": _stamped(True, EXACT_MATCH),
        "Toxicity": _stamped(True, TOXICITY),
    }
    assert run.executed_at is not None
    assert session.commit.call_count == 2  # once for the claim, once for the final write-back


def test_evaluate_is_handed_the_assignment_its_catalogue_row_and_the_entry():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)
    entry = _recorded_entry("the recorded answer")
    assignment = TestTypeAssignment(name="ROUGE", config={"threshold": "0.7"})

    with patch(_PATCH_RESOLVE_CONTENT, return_value=(entry, [(assignment, ROUGE)])), \
            patch(_PATCH_EVALUATE, side_effect=_fake_evaluate({"ROUGE"})) as mock_evaluate:
        execute_run(run_id, session)

    mock_evaluate.assert_called_once_with(assignment, ROUGE, entry, "the recorded answer")


def test_every_type_failing_rolls_up_to_red():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)
    resolved = (_recorded_entry(), [(TestTypeAssignment(name="Exact Match"), EXACT_MATCH)])

    with patch(_PATCH_RESOLVE_CONTENT, return_value=resolved), \
            patch(_PATCH_EVALUATE, side_effect=_fake_evaluate(set())):
        execute_run(run_id, session)

    assert run.status == TestStatus.red


def test_mixed_pass_fail_rolls_up_to_amber():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)
    resolved = (
        _recorded_entry(),
        [(TestTypeAssignment(name="Exact Match"), EXACT_MATCH),
         (TestTypeAssignment(name="Toxicity"), TOXICITY)],
    )

    with patch(_PATCH_RESOLVE_CONTENT, return_value=resolved), \
            patch(_PATCH_EVALUATE, side_effect=_fake_evaluate({"Exact Match"})):
        execute_run(run_id, session)

    assert run.status == TestStatus.amber
    assert run.results["Exact Match"]["passed"] is True
    assert run.results["Toxicity"]["passed"] is False


def test_one_assignments_own_evaluator_failure_does_not_fail_the_whole_run():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)
    resolved = (
        _recorded_entry(),
        [(TestTypeAssignment(name="Exact Match"), EXACT_MATCH),
         (TestTypeAssignment(name="Toxicity"), TOXICITY)],
    )

    def fake_evaluate(assignment, catalogue_row, entry, answer):
        if assignment.name == "Toxicity":
            raise RuntimeError("judge API timed out")
        return _fake_evaluate({"Exact Match"})(assignment, catalogue_row, entry, answer)

    with patch(_PATCH_RESOLVE_CONTENT, return_value=resolved), \
            patch(_PATCH_EVALUATE, side_effect=fake_evaluate):
        execute_run(run_id, session)

    # a per-assignment failure becomes that type's own detail, not NotRan -
    # and still records the engine that would have scored it
    assert run.status == TestStatus.amber
    assert run.error is None
    assert run.results["Exact Match"] == _stamped(True, EXACT_MATCH)
    assert run.results["Toxicity"] == _stamped(False, TOXICITY, detail="judge API timed out")


def test_a_type_missing_from_the_catalogue_fails_that_type_with_no_engine():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)
    resolved = (
        _recorded_entry(),
        [(TestTypeAssignment(name="Exact Match"), EXACT_MATCH),
         (TestTypeAssignment(name="Retired Type"), None)],
    )

    def fake_evaluate(assignment, catalogue_row, entry, answer):
        if catalogue_row is None:
            raise LookupError("Test type 'Retired Type' is not in the catalogue")
        return _fake_evaluate({"Exact Match"})(assignment, catalogue_row, entry, answer)

    with patch(_PATCH_RESOLVE_CONTENT, return_value=resolved), \
            patch(_PATCH_EVALUATE, side_effect=fake_evaluate):
        execute_run(run_id, session)

    assert run.status == TestStatus.amber
    assert run.results["Retired Type"] == {
        "passed": False, "score": None,
        "detail": "Test type 'Retired Type' is not in the catalogue",
        "engine": None, "engine_settings": None,
    }


# --- where the answer comes from ---


def test_a_recorded_answer_is_scored_and_copied_onto_the_run():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)
    resolved = (_recorded_entry("Go to Settings\n"),
                [(TestTypeAssignment(name="Exact Match"), EXACT_MATCH)])

    with patch(_PATCH_RESOLVE_CONTENT, return_value=resolved), \
            patch(_PATCH_GET_ANSWER) as get_answer, \
            patch(_PATCH_EVALUATE, side_effect=_fake_evaluate({"Exact Match"})):
        execute_run(run_id, session)

    get_answer.assert_not_called()
    assert run.evaluated_output == "Go to Settings\n"
    assert run.output_source == OutputSource.recorded
    assert run.status == TestStatus.green


def test_without_a_recorded_answer_the_application_is_asked_and_its_reply_scored():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)
    entry = _recorded_entry(model_output=None)
    assignment = TestTypeAssignment(name="Exact Match")
    reply = TargetResponse(answer="Go to Settings", status=200, latency_ms=12.5, attempts=1)

    with patch(_PATCH_RESOLVE_CONTENT, return_value=(entry, [(assignment, EXACT_MATCH)])), \
            patch(_PATCH_GET_ANSWER, return_value=reply) as get_answer, \
            patch(_PATCH_EVALUATE, side_effect=_fake_evaluate({"Exact Match"})) as evaluate:
        execute_run(run_id, session)

    get_answer.assert_called_once_with("How do I reset?")
    evaluate.assert_called_once_with(assignment, EXACT_MATCH, entry, "Go to Settings")
    assert run.evaluated_output == "Go to Settings"
    assert run.output_source == OutputSource.application
    assert run.status == TestStatus.green


def test_an_empty_recorded_answer_is_still_a_recorded_answer():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)
    resolved = (_recorded_entry(""), [(TestTypeAssignment(name="Exact Match"), EXACT_MATCH)])

    with patch(_PATCH_RESOLVE_CONTENT, return_value=resolved), \
            patch(_PATCH_GET_ANSWER) as get_answer, \
            patch(_PATCH_EVALUATE, side_effect=_fake_evaluate(set())):
        execute_run(run_id, session)

    get_answer.assert_not_called()
    assert (run.evaluated_output, run.output_source) == ("", OutputSource.recorded)


def test_a_failed_application_call_is_not_ran_with_the_reason_and_nothing_evaluated():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)
    entry = _recorded_entry(model_output=None)

    with patch(_PATCH_RESOLVE_CONTENT,
               return_value=(entry, [(TestTypeAssignment(name="Exact Match"), EXACT_MATCH)])), \
            patch(_PATCH_GET_ANSWER, side_effect=TargetError("application answered HTTP 503 "
                                                             "after 3 attempt(s)")), \
            patch(_PATCH_EVALUATE) as evaluate:
        execute_run(run_id, session)

    evaluate.assert_not_called()
    assert run.status == TestStatus.not_ran
    assert run.error == "application answered HTTP 503 after 3 attempt(s)"
    assert (run.results, run.evaluated_output, run.output_source) == (None, None, None)
    assert run.executed_at is not None


def test_a_failed_application_call_is_logged_as_an_error_without_a_second_traceback(caplog):
    run_id = uuid.uuid4()
    session = MagicMock()
    _mock_successful_claim(session, TestRunModel(id=run_id, status=TestStatus.running))
    entry = _recorded_entry(model_output=None)

    with (
        patch(_PATCH_RESOLVE_CONTENT,
              return_value=(entry, [(TestTypeAssignment(name="Exact Match"), EXACT_MATCH)])),
        patch(_PATCH_GET_ANSWER, side_effect=TargetError("no application configured")),
        caplog.at_level(logging.INFO, logger=_LOGGER),
    ):
        execute_run(run_id, session)

    error = next(r for r in _records(caplog) if r.levelno == logging.ERROR)
    assert error.getMessage() == f"Run {run_id} could not be executed: no application configured"
    assert error.exc_info is None


# --- _resolve_content() ---


def test_resolve_content_standalone_reads_its_frozen_copy_not_the_live_test():
    run_id = uuid.uuid4()
    live_test = TestModel(id=uuid.uuid4(), input="edited after the run")
    frozen_copy = StandaloneRunModel(
        id=run_id,
        name="greets the user",
        input="Say hello",
        test_type_assignments=[{"name": "Exact Match", "config": None}],
    )
    run = TestRunModel(id=run_id, test_id=live_test.id)
    run.test = live_test
    run.standalone_run = frozen_copy
    session = MagicMock()
    session.scalars.return_value = [EXACT_MATCH]

    entry, resolved = _resolve_content(run, session)

    assert entry is frozen_copy
    assert resolved == [(TestTypeAssignment(name="Exact Match", config=None), EXACT_MATCH)]


def test_resolve_content_test_set_entry():
    entry = TestSetEntryModel(
        id=uuid.uuid4(),
        input="Say hello",
        test_type_assignments=[
            {"name": "ROUGE", "config": {"threshold": "0.7"}},
            {"name": "Toxicity", "config": None},
        ],
    )
    run = TestRunModel(id=uuid.uuid4(), test_set_entry_id=entry.id)
    run.test_set_entry = entry
    session = MagicMock()
    # the catalogue query's order is not the assignments' order — rows are
    # matched back by name, not by position
    session.scalars.return_value = [TOXICITY, ROUGE]

    resolved_entry, resolved = _resolve_content(run, session)

    assert resolved_entry is entry
    assert resolved == [
        (TestTypeAssignment(name="ROUGE", config={"threshold": "0.7"}), ROUGE),
        (TestTypeAssignment(name="Toxicity", config=None), TOXICITY),
    ]


def test_resolve_content_leaves_none_for_a_type_the_catalogue_no_longer_has():
    entry = TestSetEntryModel(
        id=uuid.uuid4(),
        input="Say hello",
        test_type_assignments=[
            {"name": "Exact Match", "config": None},
            {"name": "Retired Type", "config": None},
        ],
    )
    run = TestRunModel(id=uuid.uuid4(), test_set_entry_id=entry.id)
    run.test_set_entry = entry
    session = MagicMock()
    session.scalars.return_value = [EXACT_MATCH]

    _, resolved = _resolve_content(run, session)

    assert resolved == [
        (TestTypeAssignment(name="Exact Match", config=None), EXACT_MATCH),
        (TestTypeAssignment(name="Retired Type", config=None), None),
    ]


# --- logging ---

_LOGGER = "assay.worker.services.execute_run"


def _records(caplog):
    return [r for r in caplog.records if r.name == _LOGGER]


def test_an_unclaimed_run_is_logged_not_silently_skipped(caplog):
    run_id = uuid.uuid4()
    session = MagicMock()
    session.execute.return_value.rowcount = 0

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        execute_run(run_id, session)

    assert [r.getMessage() for r in _records(caplog)] == [
        f"Run {run_id} not claimed: missing, already running or already terminal"
    ]


def test_a_finished_run_logs_what_ran_and_its_outcome(caplog):
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)
    resolved = (
        _recorded_entry(),
        [(TestTypeAssignment(name="Exact Match"), EXACT_MATCH),
         (TestTypeAssignment(name="Toxicity"), TOXICITY)],
    )

    with (
        patch(_PATCH_RESOLVE_CONTENT, return_value=resolved),
        patch(_PATCH_EVALUATE, side_effect=_fake_evaluate({"Exact Match"})),
        caplog.at_level(logging.INFO, logger=_LOGGER),
    ):
        execute_run(run_id, session)

    executing, finished = _records(caplog)
    assert executing.getMessage() == f"Executing run {run_id} (test set entry, 2 test types)"
    assert (executing.origin, executing.test_type_count) == ("test set entry", 2)
    assert finished.getMessage().startswith(
        f"Run {run_id} finished: Amber (1/2 passed, recorded answer) in "
    )
    assert (finished.status, finished.passed, finished.test_type_count) == ("Amber", 1, 2)
    assert finished.output_source == "recorded"
    assert isinstance(finished.duration_ms, float)


def test_a_content_resolution_failure_is_an_error_with_the_traceback(caplog):
    run_id = uuid.uuid4()
    session = MagicMock()
    _mock_successful_claim(session, TestRunModel(id=run_id, status=TestStatus.running))

    with (
        patch(_PATCH_RESOLVE_CONTENT, side_effect=RuntimeError("entry vanished")),
        caplog.at_level(logging.INFO, logger=_LOGGER),
    ):
        execute_run(run_id, session)

    (record,) = _records(caplog)
    assert record.levelno == logging.ERROR
    assert record.getMessage() == f"Run {run_id} could not be executed: entry vanished"
    assert record.exc_info[0] is RuntimeError


def test_an_evaluator_failure_is_a_warning_with_the_traceback_and_the_engine(caplog):
    run_id = uuid.uuid4()
    session = MagicMock()
    _mock_successful_claim(session, TestRunModel(id=run_id, status=TestStatus.running))
    resolved = (_recorded_entry(), [(TestTypeAssignment(name="Toxicity"), TOXICITY)])

    with (
        patch(_PATCH_RESOLVE_CONTENT, return_value=resolved),
        patch(_PATCH_EVALUATE, side_effect=RuntimeError("judge API timed out")),
        caplog.at_level(logging.INFO, logger=_LOGGER),
    ):
        execute_run(run_id, session)

    warning = next(r for r in _records(caplog) if r.levelno == logging.WARNING)
    assert warning.getMessage() == (
        f"Evaluator Toxicity (engine llm_judge) failed for run {run_id}: judge API timed out"
    )
    assert warning.exc_info[0] is RuntimeError
    assert (warning.test_type, warning.engine) == ("Toxicity", "llm_judge")
