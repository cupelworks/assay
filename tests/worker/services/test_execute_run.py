import uuid
from unittest.mock import MagicMock, patch

import pytest

from assay.models import (
    TestModel,
    TestRunModel,
    TestSetEntryModel,
    TestStatus,
    TestTypeAssignmentModel,
    TestTypesModel,
)
from assay.schemas import TestTypeAssignment, TestTypeResult
from assay.worker.services.execute_run import _resolve_content, execute_run

_PATCH_RESOLVE_CONTENT = "assay.worker.services.execute_run._resolve_content"
_PATCH_EVALUATE = "assay.worker.evaluators.evaluate"

# --- execute_run() ---


def test_run_not_found_is_a_no_op():
    session = MagicMock()
    session.scalar.return_value = None

    execute_run(uuid.uuid4(), session)

    session.commit.assert_not_called()


@pytest.mark.parametrize(
    "status", [TestStatus.green, TestStatus.amber, TestStatus.red, TestStatus.not_ran]
)
def test_already_terminal_run_is_a_no_op(status):
    run_id = uuid.uuid4()
    session = MagicMock()
    session.scalar.return_value = TestRunModel(id=run_id, status=status)

    with patch(_PATCH_EVALUATE) as mock_evaluate:
        execute_run(run_id, session)

    mock_evaluate.assert_not_called()
    session.commit.assert_not_called()


def test_pending_run_is_promoted_to_running_before_evaluation_starts():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.pending)
    session = MagicMock()
    session.scalar.return_value = run

    def _assert_running_and_raise(*_args, **_kwargs):
        assert run.status == TestStatus.running
        raise RuntimeError("stop here — only checking the pre-resolution state")

    with patch(_PATCH_RESOLVE_CONTENT, side_effect=_assert_running_and_raise):
        execute_run(run_id, session)

    # confirms the RuntimeError above was actually raised (not swallowed
    # elsewhere) and handled by the NotRan path, not silently skipped
    assert run.status == TestStatus.not_ran


def test_content_resolution_failure_marks_not_ran():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.pending)
    session = MagicMock()
    session.scalar.return_value = run

    with patch(_PATCH_RESOLVE_CONTENT, side_effect=RuntimeError("entry vanished")):
        execute_run(run_id, session)

    assert run.status == TestStatus.not_ran
    assert run.error == "entry vanished"
    assert run.results is None
    assert run.executed_at is not None


def test_every_type_passing_rolls_up_to_green():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.pending)
    session = MagicMock()
    session.scalar.return_value = run

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
    assert session.commit.call_count == 2  # once for `running`, once for the final write-back


def test_every_type_failing_rolls_up_to_red():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.pending)
    session = MagicMock()
    session.scalar.return_value = run

    assignment = TestTypeAssignment(name="Exact Match")
    failing_result = TestTypeResult(passed=False, score=None, detail=None)
    resolved = (MagicMock(), [(assignment, "deterministic")])

    with patch(_PATCH_RESOLVE_CONTENT, return_value=resolved), \
            patch(_PATCH_EVALUATE, return_value=failing_result):
        execute_run(run_id, session)

    assert run.status == TestStatus.red


def test_mixed_pass_fail_rolls_up_to_amber():
    run_id = uuid.uuid4()
    run = TestRunModel(id=run_id, status=TestStatus.pending)
    session = MagicMock()
    session.scalar.return_value = run

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
    run = TestRunModel(id=run_id, status=TestStatus.pending)
    session = MagicMock()
    session.scalar.return_value = run

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


def test_resolve_content_standalone():
    test_type = TestTypesModel(name="Exact Match", category="deterministic")
    assignment = TestTypeAssignmentModel(test_type_name="Exact Match", config=None)
    assignment.test_type = test_type
    test = TestModel(
        id=uuid.uuid4(),
        input="Say hello",
        test_type_assignments=[assignment],
    )
    run = TestRunModel(id=uuid.uuid4(), test_id=test.id)
    run.test = test
    session = MagicMock()

    entry, resolved = _resolve_content(run, session)

    assert entry is test
    assert resolved == [(TestTypeAssignment(name="Exact Match", config=None), "deterministic")]
    session.execute.assert_not_called()  # no join needed — category came from the relationship


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
