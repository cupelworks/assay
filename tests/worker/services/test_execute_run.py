import logging
import uuid
from unittest.mock import MagicMock, patch

from assay.models import (
    StandaloneRunModel,
    TestModel,
    TestRunModel,
    TestSetEntryModel,
    TestStatus,
)
from assay.schemas import TestTypeAssignment, TestTypeResult
from assay.worker.services.execute_run import _resolve_content, execute_run

_PATCH_RESOLVE_CONTENT = "assay.worker.services.execute_run._resolve_content"
_PATCH_EVALUATE = "assay.worker.evaluators.evaluate"


def _mock_successful_claim(session: MagicMock, run: TestRunModel) -> None:
    """Simulates the atomic claim UPDATE matching exactly one row, and the
    follow-up fetch returning that same run.
    """
    session.execute.return_value.rowcount = 1
    session.scalar.return_value = run


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

    assignment_a = TestTypeAssignment(name="Exact Match")
    assignment_b = TestTypeAssignment(name="Toxicity")

    with patch(_PATCH_RESOLVE_CONTENT, return_value=(
            MagicMock(),
            [(assignment_a, "deterministic"), (assignment_b, "llm_as_judge")],
    )), patch(_PATCH_EVALUATE, return_value=TestTypeResult(passed=True, score=None, detail=None)):
        execute_run(run_id, session)

    assert run.status == TestStatus.green
    assert run.results == {
        "Exact Match": {"passed": True, "score": None, "detail": None},
        "Toxicity": {"passed": True, "score": None, "detail": None},
    }
    assert run.executed_at is not None
    assert session.commit.call_count == 2  # once for the claim, once for the final write-back


def test_every_type_failing_rolls_up_to_red():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)

    assignment = TestTypeAssignment(name="Exact Match")
    failing_result = TestTypeResult(passed=False, score=None, detail=None)
    resolved = (MagicMock(), [(assignment, "deterministic")])

    with patch(_PATCH_RESOLVE_CONTENT, return_value=resolved), \
            patch(_PATCH_EVALUATE, return_value=failing_result):
        execute_run(run_id, session)

    assert run.status == TestStatus.red


def test_mixed_pass_fail_rolls_up_to_amber():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)

    assignment_a = TestTypeAssignment(name="Exact Match")
    assignment_b = TestTypeAssignment(name="Toxicity")

    def fake_evaluate(assignment, category, entry):
        return TestTypeResult(passed=assignment.name == "Exact Match", score=None, detail=None)

    with patch(_PATCH_RESOLVE_CONTENT, return_value=(
            MagicMock(),
            [(assignment_a, "deterministic"), (assignment_b, "llm_as_judge")],
    )), patch(_PATCH_EVALUATE, side_effect=fake_evaluate):
        execute_run(run_id, session)

    assert run.status == TestStatus.amber
    assert run.results["Exact Match"]["passed"] is True
    assert run.results["Toxicity"]["passed"] is False


def test_one_assignments_own_evaluator_failure_does_not_fail_the_whole_run():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.running)
    session = MagicMock()
    _mock_successful_claim(session, run)

    assignment_a = TestTypeAssignment(name="Exact Match")
    assignment_b = TestTypeAssignment(name="Toxicity")

    def fake_evaluate(assignment, category, entry):
        if assignment.name == "Toxicity":
            raise RuntimeError("judge API timed out")
        return TestTypeResult(passed=True, score=None, detail=None)

    with patch(_PATCH_RESOLVE_CONTENT, return_value=(
            MagicMock(),
            [(assignment_a, "deterministic"), (assignment_b, "llm_as_judge")],
    )), patch(_PATCH_EVALUATE, side_effect=fake_evaluate):
        execute_run(run_id, session)

    # a per-assignment failure becomes that type's own detail, not NotRan
    assert run.status == TestStatus.amber
    assert run.error is None
    assert run.results["Exact Match"] == {"passed": True, "score": None, "detail": None}
    assert run.results["Toxicity"] == {
        "passed": False, "score": None, "detail": "judge API timed out",
    }


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
    session.execute.return_value.tuples.return_value.all.return_value = [
        ("Exact Match", "deterministic"),
    ]

    entry, resolved = _resolve_content(run, session)

    assert entry is frozen_copy
    assert resolved == [(TestTypeAssignment(name="Exact Match", config=None), "deterministic")]


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
    session.execute.return_value.tuples.return_value.all.return_value = [
        ("ROUGE", "nlp_metric"),
        ("Toxicity", "llm_as_judge"),
    ]

    resolved_entry, resolved = _resolve_content(run, session)

    assert resolved_entry is entry
    assert resolved == [
        (TestTypeAssignment(name="ROUGE", config={"threshold": "0.7"}), "nlp_metric"),
        (TestTypeAssignment(name="Toxicity", config=None), "llm_as_judge"),
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
        MagicMock(),
        [(TestTypeAssignment(name="Exact Match"), "deterministic"),
         (TestTypeAssignment(name="Toxicity"), "llm_as_judge")],
    )

    def fake_evaluate(assignment, category, entry):
        return TestTypeResult(passed=assignment.name == "Exact Match", score=None, detail=None)

    with (
        patch(_PATCH_RESOLVE_CONTENT, return_value=resolved),
        patch(_PATCH_EVALUATE, side_effect=fake_evaluate),
        caplog.at_level(logging.INFO, logger=_LOGGER),
    ):
        execute_run(run_id, session)

    executing, finished = _records(caplog)
    assert executing.getMessage() == f"Executing run {run_id} (test set entry, 2 test types)"
    assert (executing.origin, executing.test_type_count) == ("test set entry", 2)
    assert finished.getMessage().startswith(f"Run {run_id} finished: Amber (1/2 passed) in ")
    assert (finished.status, finished.passed, finished.test_type_count) == ("Amber", 1, 2)
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


def test_an_evaluator_failure_is_a_warning_with_the_traceback(caplog):
    run_id = uuid.uuid4()
    session = MagicMock()
    _mock_successful_claim(session, TestRunModel(id=run_id, status=TestStatus.running))
    resolved = (MagicMock(), [(TestTypeAssignment(name="Toxicity"), "llm_as_judge")])

    with (
        patch(_PATCH_RESOLVE_CONTENT, return_value=resolved),
        patch(_PATCH_EVALUATE, side_effect=RuntimeError("judge API timed out")),
        caplog.at_level(logging.INFO, logger=_LOGGER),
    ):
        execute_run(run_id, session)

    warning = next(r for r in _records(caplog) if r.levelno == logging.WARNING)
    assert warning.getMessage() == (
        f"Evaluator Toxicity (llm_as_judge) failed for run {run_id}: judge API timed out"
    )
    assert warning.exc_info[0] is RuntimeError
    assert (warning.test_type, warning.category) == ("Toxicity", "llm_as_judge")
