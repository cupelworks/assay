import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.models import TestRunModel, TestStatus
from assay.schemas import (
    StandaloneRunDetails,
    TestCaseID,
    TestCaseSnapshotDate,
    TestPlanExecutionID,
    TestPlanExecutionRunDetails,
    TestPlanID,
    TestSetEntryID,
    TestSetExecutionID,
    TestSetExecutionRunDetails,
    TestSetID,
    TestTypeResult,
)
from assay.services import (
    get_run_details_by_test_and_run_id,
    get_run_details_by_test_plan_execution_and_run_id,
    get_run_details_by_test_set_execution_and_run_id,
)

# --- get_run_details_by_test_and_run_id() ---

def test_get_run_details_by_test_and_run_id_test_id_not_found():
    test_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_and_run_id(test_id, uuid.uuid4(), session))

    session.scalar.assert_called_once()
    assert e.value.status_code == 404
    assert f"Test with id {str(test_id)} not found" in str(e.value.detail)


def test_get_run_details_by_test_and_run_id_test_run_id_not_found():
    test_id = uuid.uuid4()
    test_run_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [test_id, None]

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_and_run_id(test_id, test_run_id, session))

    assert session.scalar.call_count == 2
    assert e.value.status_code == 404
    assert f"Test run with ID '{test_run_id}' does not exist" in str(e.value.detail)


def test_get_run_details_by_test_and_run_id_test_run_id_not_linked_to_specific_test_id():
    test_id = uuid.uuid4()
    test_run_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [test_id, test_run_id, None]

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_and_run_id(test_id, test_run_id, session))

    assert session.scalar.call_count == 3
    assert e.value.status_code == 404
    assert (f"Test run with ID '{test_run_id}' not linked "
            f"to test with ID '{test_id}'") in str(e.value.detail)


def test_get_run_details_by_test_and_run_id_happy_path():
    test_id = uuid.uuid4()
    test_run_id = uuid.uuid4()
    
    session = AsyncMock()

    results = {
        "BLEU": {"passed": True, "score": 0.5, "detail": None},
        "ROUGE": {"passed": True, "score": 0.9, "detail": None},
    }
    
    returned_test_run_model_for_validation = TestRunModel(
        id=test_run_id,
        status=TestStatus.green,
        created_at=datetime.now().astimezone(),
        test_id=test_id,
        results=results,
        error=None,
        executed_at=datetime.now().astimezone(),
    )
    session.scalar.side_effect = [
        test_id, 
        test_run_id, 
        TestRunModel(id=test_run_id),
        returned_test_run_model_for_validation
    ]

    response = asyncio.run(get_run_details_by_test_and_run_id(test_id, test_run_id, session))

    assert session.scalar.call_count == 4
    assert response == StandaloneRunDetails(
        id=returned_test_run_model_for_validation.id,
        status=returned_test_run_model_for_validation.status,
        created_at=returned_test_run_model_for_validation.created_at,
        test_case_id=TestCaseID(id=test_id),
        results=returned_test_run_model_for_validation.results,
        error=returned_test_run_model_for_validation.error,
        executed_at=returned_test_run_model_for_validation.executed_at,
    )


def test_get_run_details_by_test_and_run_id_happy_path_non_terminal_run():
    test_id = uuid.uuid4()
    test_run_id = uuid.uuid4()

    session = AsyncMock()

    returned_test_run_model_for_validation = TestRunModel(
        id=test_run_id,
        status=TestStatus.pending,
        created_at=datetime.now().astimezone(),
        test_id=test_id,
        results=None,
        error=None,
        executed_at=None,
    )
    session.scalar.side_effect = [
        test_id,
        test_run_id,
        TestRunModel(id=test_run_id),
        returned_test_run_model_for_validation,
    ]

    response = asyncio.run(get_run_details_by_test_and_run_id(test_id, test_run_id, session))

    assert session.scalar.call_count == 4
    assert response == StandaloneRunDetails(
        id=returned_test_run_model_for_validation.id,
        status=TestStatus.pending,
        created_at=returned_test_run_model_for_validation.created_at,
        test_case_id=TestCaseID(id=test_id),
        results=None,
        error=None,
        executed_at=None,
    )


# --- get_run_details_by_test_set_execution_and_run_id() ---

def test_get_run_details_by_test_set_execution_and_run_id_test_set_id_not_found():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_set_execution_and_run_id(
            test_set_id, uuid.uuid4(), uuid.uuid4(), session
        ))

    session.scalar.assert_called_once()
    assert e.value.status_code == 404
    assert f"Test set with ID '{test_set_id}' not found" in str(e.value.detail)


def test_get_run_details_by_test_set_execution_and_run_id_execution_id_not_found():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [test_set_id, None]

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_set_execution_and_run_id(
            test_set_id, test_set_execution_id, uuid.uuid4(), session
        ))

    assert session.scalar.call_count == 2
    assert e.value.status_code == 404
    assert (f"Test set execution with ID '{test_set_execution_id}' does not exist"
            in str(e.value.detail))


def test_get_run_details_by_test_set_execution_and_run_id_execution_not_linked_to_test_set():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [test_set_id, test_set_execution_id, None]

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_set_execution_and_run_id(
            test_set_id, test_set_execution_id, uuid.uuid4(), session
        ))

    assert session.scalar.call_count == 3
    assert e.value.status_code == 404
    assert (f"Test set execution with ID '{test_set_execution_id}' not linked "
            f"to test set with ID '{test_set_id}'") in str(e.value.detail)


def test_get_run_details_by_test_set_execution_and_run_id_test_run_id_not_found():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()
    test_run_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [test_set_id, test_set_execution_id, test_set_execution_id, None]

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_set_execution_and_run_id(
            test_set_id, test_set_execution_id, test_run_id, session
        ))

    assert session.scalar.call_count == 4
    assert e.value.status_code == 404
    assert f"Test run with ID '{test_run_id}' does not exist" in str(e.value.detail)


def test_get_run_details_by_test_set_execution_and_run_id_run_not_linked_to_execution():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()
    test_run_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        test_set_id, test_set_execution_id, test_set_execution_id, test_run_id, None
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_set_execution_and_run_id(
            test_set_id, test_set_execution_id, test_run_id, session
        ))

    assert session.scalar.call_count == 5
    assert e.value.status_code == 404
    assert (f"Test run with ID '{test_run_id}' not linked "
            f"to test set execution with ID '{test_set_execution_id}'") in str(e.value.detail)


def test_get_run_details_by_test_set_execution_and_run_id_happy_path():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()
    test_run_id = uuid.uuid4()
    test_set_entry_id = uuid.uuid4()
    test_case_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        test_set_id, test_set_execution_id, test_set_execution_id, test_run_id, test_run_id
    ]

    results = {
        "BLEU": {"passed": True, "score": 0.5, "detail": None},
        "ROUGE": {"passed": True, "score": 0.9, "detail": None},
    }
    created_at = datetime.now().astimezone()
    executed_at = datetime.now().astimezone()
    snapshot_at = datetime.now().astimezone()

    row = SimpleNamespace(
        status=TestStatus.green,
        created_at=created_at,
        test_set_entry_id=test_set_entry_id,
        results=results,
        error=None,
        executed_at=executed_at,
        test_id=test_case_id,
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output="Hello, Alice!",
        test_type_assignments=[
            {"name": "exact_match", "config": None}, {"name": "bleu", "config": None}
        ],
        snapshot_at=snapshot_at,
    )
    result = MagicMock()
    result.one.return_value = row
    session.execute.return_value = result

    response = asyncio.run(get_run_details_by_test_set_execution_and_run_id(
        test_set_id, test_set_execution_id, test_run_id, session
    ))

    assert session.scalar.call_count == 5
    session.execute.assert_called_once()
    assert response == TestSetExecutionRunDetails(
        id=test_run_id,
        status=TestStatus.green,
        created_at=created_at,
        test_set_entry_id=TestSetEntryID(id=test_set_entry_id),
        test_set_execution_id=TestSetExecutionID(id=test_set_execution_id),
        results=results,
        error=None,
        executed_at=executed_at,
        test_case_id=TestCaseID(id=test_case_id),
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output="Hello, Alice!",
        test_type_assignments=[
            {"name": "exact_match", "config": None}, {"name": "bleu", "config": None}
        ],
        test_case_snapshot_at=TestCaseSnapshotDate(snapshot_at=snapshot_at),
        test_set_id=TestSetID(id=test_set_id),
    )


def test_get_run_details_by_test_set_execution_and_run_id_reachable_after_unlink():
    """Regression test for basic_api_implementation/dev_notes.md note 22: the entry lookup must not
    filter on the entry's current test_set_id, so a run's detail stays
    reachable even after its entry has been unlinked from the test set
    (PATCH /test-sets/{test_set_id}/entries nulls TestSetEntryModel.test_set_id).

    The mocked session returns a canned row regardless of the query's WHERE
    clauses, so it can't tell an unlinked entry apart from a linked one by
    itself — the real assertion here is on the query that was actually
    executed: it must not reference test_set_entries.test_set_id at all,
    which is what excluded an unlinked entry's row before this was fixed.
    """
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()
    test_run_id = uuid.uuid4()
    test_set_entry_id = uuid.uuid4()
    test_case_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        test_set_id, test_set_execution_id, test_set_execution_id, test_run_id, test_run_id
    ]

    results = {"BLEU": TestTypeResult(passed=True, score=0.5, detail=None)}
    created_at = datetime.now().astimezone()
    executed_at = datetime.now().astimezone()
    snapshot_at = datetime.now().astimezone()

    row = SimpleNamespace(
        status=TestStatus.green,
        created_at=created_at,
        test_set_entry_id=test_set_entry_id,
        results=results,
        error=None,
        executed_at=executed_at,
        test_id=test_case_id,
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output="Hello, Alice!",
        test_type_assignments=[{"name": "bleu", "config": None}],
        snapshot_at=snapshot_at,
    )
    result = MagicMock()
    result.one.return_value = row
    session.execute.return_value = result

    response = asyncio.run(get_run_details_by_test_set_execution_and_run_id(
        test_set_id, test_set_execution_id, test_run_id, session
    ))

    executed_stmt = session.execute.call_args[0][0]
    compiled_sql = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "test_set_entries.test_set_id" not in compiled_sql

    assert response.test_set_entry_id == TestSetEntryID(id=test_set_entry_id)
    assert response.results == results


def test_get_run_details_by_test_set_execution_and_run_id_happy_path_non_terminal_run():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()
    test_run_id = uuid.uuid4()
    test_set_entry_id = uuid.uuid4()
    test_case_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        test_set_id, test_set_execution_id, test_set_execution_id, test_run_id, test_run_id
    ]

    created_at = datetime.now().astimezone()
    snapshot_at = datetime.now().astimezone()

    row = SimpleNamespace(
        status=TestStatus.pending,
        created_at=created_at,
        test_set_entry_id=test_set_entry_id,
        results=None,
        error=None,
        executed_at=None,
        test_id=test_case_id,
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output=None,
        test_type_assignments=[{"name": "exact_match", "config": None}],
        snapshot_at=snapshot_at,
    )
    result = MagicMock()
    result.one.return_value = row
    session.execute.return_value = result

    response = asyncio.run(get_run_details_by_test_set_execution_and_run_id(
        test_set_id, test_set_execution_id, test_run_id, session
    ))

    assert session.scalar.call_count == 5
    assert response == TestSetExecutionRunDetails(
        id=test_run_id,
        status=TestStatus.pending,
        created_at=created_at,
        test_set_entry_id=TestSetEntryID(id=test_set_entry_id),
        test_set_execution_id=TestSetExecutionID(id=test_set_execution_id),
        results=None,
        error=None,
        executed_at=None,
        test_case_id=TestCaseID(id=test_case_id),
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output=None,
        test_type_assignments=[{"name": "exact_match", "config": None}],
        test_case_snapshot_at=TestCaseSnapshotDate(snapshot_at=snapshot_at),
        test_set_id=TestSetID(id=test_set_id),
    )


# --- get_run_details_by_test_plan_execution_and_run_id() ---

def test_get_run_details_by_test_plan_execution_and_run_id_test_plan_id_not_found():
    test_plan_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_plan_execution_and_run_id(
            test_plan_id, uuid.uuid4(), uuid.uuid4(), session
        ))

    session.scalar.assert_called_once()
    assert e.value.status_code == 404
    assert f"Test plan with ID '{test_plan_id}' not found" in str(e.value.detail)


def test_get_run_details_by_test_plan_execution_and_run_id_execution_id_not_found():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [test_plan_id, None]

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_plan_execution_and_run_id(
            test_plan_id, test_plan_execution_id, uuid.uuid4(), session
        ))

    assert session.scalar.call_count == 2
    assert e.value.status_code == 404
    assert (f"Test plan execution with ID '{test_plan_execution_id}' does not exist"
            in str(e.value.detail))


def test_get_run_details_by_test_plan_execution_and_run_id_execution_not_linked_to_test_plan():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [test_plan_id, test_plan_execution_id, None]

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_plan_execution_and_run_id(
            test_plan_id, test_plan_execution_id, uuid.uuid4(), session
        ))

    assert session.scalar.call_count == 3
    assert e.value.status_code == 404
    assert (f"Test plan execution with ID '{test_plan_execution_id}' not linked "
            f"to test plan with ID '{test_plan_id}'") in str(e.value.detail)


def test_get_run_details_by_test_plan_execution_and_run_id_test_run_id_not_found():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()
    test_run_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        test_plan_id, test_plan_execution_id, test_plan_execution_id, None
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_plan_execution_and_run_id(
            test_plan_id, test_plan_execution_id, test_run_id, session
        ))

    assert session.scalar.call_count == 4
    assert e.value.status_code == 404
    assert f"Test run with ID '{test_run_id}' does not exist" in str(e.value.detail)


def test_get_run_details_by_test_plan_execution_and_run_id_run_not_linked_to_execution():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()
    test_run_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        test_plan_id, test_plan_execution_id, test_plan_execution_id, test_run_id, None
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_run_details_by_test_plan_execution_and_run_id(
            test_plan_id, test_plan_execution_id, test_run_id, session
        ))

    assert session.scalar.call_count == 5
    assert e.value.status_code == 404
    assert (f"Test run with ID '{test_run_id}' not linked "
            f"to test plan execution with ID '{test_plan_execution_id}'") in str(e.value.detail)


def test_get_run_details_by_test_plan_execution_and_run_id_happy_path():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()
    test_run_id = uuid.uuid4()
    test_set_entry_id = uuid.uuid4()
    test_set_id = uuid.uuid4()
    test_case_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        test_plan_id, test_plan_execution_id, test_plan_execution_id, test_run_id, test_run_id
    ]

    results = {
        "BLEU": {"passed": True, "score": 0.5, "detail": None},
        "ROUGE": {"passed": True, "score": 0.9, "detail": None},
    }
    created_at = datetime.now().astimezone()
    executed_at = datetime.now().astimezone()
    snapshot_at = datetime.now().astimezone()

    row = SimpleNamespace(
        status=TestStatus.green,
        created_at=created_at,
        test_set_entry_id=test_set_entry_id,
        results=results,
        error=None,
        executed_at=executed_at,
        test_id=test_case_id,
        test_set_id=test_set_id,
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output="Hello, Alice!",
        test_type_assignments=[
            {"name": "exact_match", "config": None}, {"name": "bleu", "config": None}
        ],
        snapshot_at=snapshot_at,
    )
    result = MagicMock()
    result.one.return_value = row
    session.execute.return_value = result

    response = asyncio.run(get_run_details_by_test_plan_execution_and_run_id(
        test_plan_id, test_plan_execution_id, test_run_id, session
    ))

    assert session.scalar.call_count == 5
    session.execute.assert_called_once()
    assert response == TestPlanExecutionRunDetails(
        id=test_run_id,
        status=TestStatus.green,
        created_at=created_at,
        test_set_entry_id=TestSetEntryID(id=test_set_entry_id),
        test_plan_execution_id=TestPlanExecutionID(id=test_plan_execution_id),
        results=results,
        error=None,
        executed_at=executed_at,
        test_case_id=TestCaseID(id=test_case_id),
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output="Hello, Alice!",
        test_type_assignments=[
            {"name": "exact_match", "config": None}, {"name": "bleu", "config": None}
        ],
        test_case_snapshot_at=TestCaseSnapshotDate(snapshot_at=snapshot_at),
        test_set_id=TestSetID(id=test_set_id),
        test_plan_id=TestPlanID(id=test_plan_id),
    )


def test_get_run_details_by_test_plan_execution_and_run_id_happy_path_non_terminal_run():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()
    test_run_id = uuid.uuid4()
    test_set_entry_id = uuid.uuid4()
    test_set_id = uuid.uuid4()
    test_case_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        test_plan_id, test_plan_execution_id, test_plan_execution_id, test_run_id, test_run_id
    ]

    created_at = datetime.now().astimezone()
    snapshot_at = datetime.now().astimezone()

    row = SimpleNamespace(
        status=TestStatus.pending,
        created_at=created_at,
        test_set_entry_id=test_set_entry_id,
        results=None,
        error=None,
        executed_at=None,
        test_id=test_case_id,
        test_set_id=test_set_id,
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output=None,
        test_type_assignments=[{"name": "exact_match", "config": None}],
        snapshot_at=snapshot_at,
    )
    result = MagicMock()
    result.one.return_value = row
    session.execute.return_value = result

    response = asyncio.run(get_run_details_by_test_plan_execution_and_run_id(
        test_plan_id, test_plan_execution_id, test_run_id, session
    ))

    assert session.scalar.call_count == 5
    assert response == TestPlanExecutionRunDetails(
        id=test_run_id,
        status=TestStatus.pending,
        created_at=created_at,
        test_set_entry_id=TestSetEntryID(id=test_set_entry_id),
        test_plan_execution_id=TestPlanExecutionID(id=test_plan_execution_id),
        results=None,
        error=None,
        executed_at=None,
        test_case_id=TestCaseID(id=test_case_id),
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output=None,
        test_type_assignments=[{"name": "exact_match", "config": None}],
        test_case_snapshot_at=TestCaseSnapshotDate(snapshot_at=snapshot_at),
        test_set_id=TestSetID(id=test_set_id),
        test_plan_id=TestPlanID(id=test_plan_id),
    )


def test_get_run_details_by_test_plan_execution_and_run_id_reachable_after_unlink():
    """Regression test mirroring the test-set version's note-22 fix, one layer
    up: the entry lookup must not filter on the entry's current test_set_id,
    and must not join TestPlanEntryModel at all, so a run's detail stays
    reachable even after its entry has been unlinked from its test set, or
    that test set has since been unlinked from this plan
    (basic_api_implementation/dev_notes.md note 4 — test-plan-to-set links
    never freeze).

    The mocked session returns a canned row regardless of the query's WHERE
    clauses, so the real assertion is on the query that was actually
    executed: it must not reference test_set_entries.test_set_id or the
    test_plan_entries table at all. test_set_id in the response is expected
    to be None here, simulating an unlinked entry.
    """
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()
    test_run_id = uuid.uuid4()
    test_set_entry_id = uuid.uuid4()
    test_case_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        test_plan_id, test_plan_execution_id, test_plan_execution_id, test_run_id, test_run_id
    ]

    results = {"BLEU": TestTypeResult(passed=True, score=0.5, detail=None)}
    created_at = datetime.now().astimezone()
    executed_at = datetime.now().astimezone()
    snapshot_at = datetime.now().astimezone()

    row = SimpleNamespace(
        status=TestStatus.green,
        created_at=created_at,
        test_set_entry_id=test_set_entry_id,
        results=results,
        error=None,
        executed_at=executed_at,
        test_id=test_case_id,
        test_set_id=None,
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output="Hello, Alice!",
        test_type_assignments=[{"name": "bleu", "config": None}],
        snapshot_at=snapshot_at,
    )
    result = MagicMock()
    result.one.return_value = row
    session.execute.return_value = result

    response = asyncio.run(get_run_details_by_test_plan_execution_and_run_id(
        test_plan_id, test_plan_execution_id, test_run_id, session
    ))

    executed_stmt = session.execute.call_args[0][0]
    compiled_sql = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
    # test_set_entries.test_set_id legitimately appears in the SELECT list
    # (it backs the nullable test_set_id response field) — the regression
    # check is that it's never used to *filter* the query, which would
    # exclude an unlinked entry's row the way note 22 did originally.
    where_sql = str(executed_stmt.whereclause.compile(compile_kwargs={"literal_binds": True}))
    assert "test_set_id" not in where_sql
    assert "test_plan_entries" not in compiled_sql

    assert response.test_set_entry_id == TestSetEntryID(id=test_set_entry_id)
    assert response.test_set_id is None
    assert response.results == results