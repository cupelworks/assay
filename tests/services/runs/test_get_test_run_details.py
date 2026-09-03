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
    TestSetEntryID,
    TestSetExecutionID,
    TestSetExecutionRunDetails,
    TestSetID,
)
from assay.services import (
    get_run_details_by_test_and_run_id,
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

    scores = {"bleu": 0.5, "rogue": 0.9}
    
    returned_test_run_model_for_validation = TestRunModel(
        id=test_run_id,
        status=TestStatus.completed,
        created_at=datetime.now().astimezone(),
        test_id=test_id,
        scores=scores,
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
        scores=returned_test_run_model_for_validation.scores,
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
        scores=None,
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
        scores=None,
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

    scores = {"bleu": 0.5, "rogue": 0.9}
    created_at = datetime.now().astimezone()
    executed_at = datetime.now().astimezone()
    snapshot_at = datetime.now().astimezone()

    row = SimpleNamespace(
        status=TestStatus.completed,
        created_at=created_at,
        test_set_entry_id=test_set_entry_id,
        scores=scores,
        error=None,
        executed_at=executed_at,
        test_id=test_case_id,
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output="Hello, Alice!",
        test_type_names=["exact_match", "bleu"],
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
        status=TestStatus.completed,
        created_at=created_at,
        test_set_entry_id=TestSetEntryID(id=test_set_entry_id),
        test_set_execution_id=TestSetExecutionID(id=test_set_execution_id),
        scores=scores,
        error=None,
        executed_at=executed_at,
        test_case_id=TestCaseID(id=test_case_id),
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output="Hello, Alice!",
        test_type_names=["exact_match", "bleu"],
        test_case_snapshot_at=TestCaseSnapshotDate(snapshot_at=snapshot_at),
        test_set_id=TestSetID(id=test_set_id),
    )


def test_get_run_details_by_test_set_execution_and_run_id_reachable_after_unlink():
    """Regression test for dev_notes.md note 22: the entry lookup must not
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

    scores = {"bleu": 0.5}
    created_at = datetime.now().astimezone()
    executed_at = datetime.now().astimezone()
    snapshot_at = datetime.now().astimezone()

    row = SimpleNamespace(
        status=TestStatus.completed,
        created_at=created_at,
        test_set_entry_id=test_set_entry_id,
        scores=scores,
        error=None,
        executed_at=executed_at,
        test_id=test_case_id,
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output="Hello, Alice!",
        test_type_names=["bleu"],
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
    assert response.scores == scores


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
        scores=None,
        error=None,
        executed_at=None,
        test_id=test_case_id,
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output=None,
        test_type_names=["exact_match"],
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
        scores=None,
        error=None,
        executed_at=None,
        test_case_id=TestCaseID(id=test_case_id),
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output=None,
        test_type_names=["exact_match"],
        test_case_snapshot_at=TestCaseSnapshotDate(snapshot_at=snapshot_at),
        test_set_id=TestSetID(id=test_set_id),
    )