import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.models import OutputSource, TestRunModel, TestStatus
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


def _standalone_session(test_id, test_run_id, row):
    session = AsyncMock()
    session.scalar.side_effect = [test_id, test_run_id, TestRunModel(id=test_run_id)]
    result = MagicMock()
    result.one.return_value = row
    session.execute.return_value = result
    return session


def _standalone_row(**overrides):
    fields = dict(
        status=TestStatus.green,
        created_at=datetime.now().astimezone(),
        results={
            "BLEU": {"passed": True, "score": 0.5, "detail": None},
            "ROUGE": {"passed": True, "score": 0.9, "detail": None},
        },
        error=None,
        evaluated_output=None,
        output_source=None,
        application_reply=None,
        executed_at=datetime.now().astimezone(),
        batch_id=None,
        batch_index=None,
        name="greets the user by name",
        input="Say hello to Alice.",
        expected_output="Hello, Alice!",
        model_output="Hello, Alice!",
        test_type_assignments=[
            {"name": "BLEU", "config": {"threshold": "0.4"}},
            {"name": "ROUGE", "config": {"threshold": "0.7"}},
        ],
        snapshot_at=datetime.now().astimezone(),
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_get_run_details_by_test_and_run_id_happy_path():
    test_id = uuid.uuid4()
    test_run_id = uuid.uuid4()
    row = _standalone_row()
    session = _standalone_session(test_id, test_run_id, row)

    response = asyncio.run(get_run_details_by_test_and_run_id(test_id, test_run_id, session))

    assert session.scalar.call_count == 3
    session.execute.assert_called_once()
    assert response == StandaloneRunDetails(
        id=test_run_id,
        status=row.status,
        created_at=row.created_at,
        test_case_id=TestCaseID(id=test_id),
        results=row.results,
        error=None,
        evaluated_output=None,
        output_source=None,
        application_reply=None,
        executed_at=row.executed_at,
        name=row.name,
        input=row.input,
        expected_output=row.expected_output,
        model_output=row.model_output,
        test_type_assignments=row.test_type_assignments,
        test_case_snapshot_at=TestCaseSnapshotDate(snapshot_at=row.snapshot_at),
    )


def test_get_run_details_by_test_and_run_id_reads_the_frozen_copy():
    # The content comes from the run's StandaloneRunModel (joined in the
    # same query), never from the live test - that's what keeps an edited
    # test from rewriting what an old run was judged against.
    test_id = uuid.uuid4()
    test_run_id = uuid.uuid4()
    session = _standalone_session(test_id, test_run_id, _standalone_row())

    asyncio.run(get_run_details_by_test_and_run_id(test_id, test_run_id, session))

    sql = str(session.execute.call_args.args[0])
    assert "JOIN standalone_runs ON test_runs.id = standalone_runs.id" in sql
    assert "standalone_runs.expected_output" in sql
    assert "tests." not in sql


def test_get_run_details_by_test_and_run_id_happy_path_non_terminal_run():
    test_id = uuid.uuid4()
    test_run_id = uuid.uuid4()
    row = _standalone_row(status=TestStatus.pending, results=None, executed_at=None)
    session = _standalone_session(test_id, test_run_id, row)

    response = asyncio.run(get_run_details_by_test_and_run_id(test_id, test_run_id, session))

    assert response.status == TestStatus.pending
    assert (response.results, response.error, response.executed_at) == (None, None, None)
    assert response.input == "Say hello to Alice."  # the copy exists from creation on


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
        batch_id=None,
        batch_index=None,
        test_set_entry_id=test_set_entry_id,
        results=results,
        error=None,
        evaluated_output=None,
        output_source=None,
        application_reply=None,
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
        evaluated_output=None,
        output_source=None,
        application_reply=None,
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
    """Regression test: the entry lookup must not filter on the entry's
    current test_set_id, so a run's detail stays
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
        batch_id=None,
        batch_index=None,
        test_set_entry_id=test_set_entry_id,
        results=results,
        error=None,
        evaluated_output=None,
        output_source=None,
        application_reply=None,
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
        batch_id=None,
        batch_index=None,
        test_set_entry_id=test_set_entry_id,
        results=None,
        error=None,
        evaluated_output=None,
        output_source=None,
        application_reply=None,
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
        evaluated_output=None,
        output_source=None,
        application_reply=None,
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
        batch_id=None,
        batch_index=None,
        test_set_entry_id=test_set_entry_id,
        results=results,
        error=None,
        evaluated_output=None,
        output_source=None,
        application_reply=None,
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
        evaluated_output=None,
        output_source=None,
        application_reply=None,
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
        batch_id=None,
        batch_index=None,
        test_set_entry_id=test_set_entry_id,
        results=None,
        error=None,
        evaluated_output=None,
        output_source=None,
        application_reply=None,
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
        evaluated_output=None,
        output_source=None,
        application_reply=None,
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
    """Regression test mirroring the test-set version's unlink fix, one layer
    up: the entry lookup must not filter on the entry's current test_set_id,
    and must not join TestPlanEntryModel at all, so a run's detail stays
    reachable even after its entry has been unlinked from its test set, or
    that test set has since been unlinked from this plan (test-plan-to-set
    links never freeze).

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
        batch_id=None,
        batch_index=None,
        test_set_entry_id=test_set_entry_id,
        results=results,
        error=None,
        evaluated_output=None,
        output_source=None,
        application_reply=None,
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
    # exclude an unlinked entry's row, as the original query did.
    where_sql = str(executed_stmt.whereclause.compile(compile_kwargs={"literal_binds": True}))
    assert "test_set_id" not in where_sql
    assert "test_plan_entries" not in compiled_sql

    assert response.test_set_entry_id == TestSetEntryID(id=test_set_entry_id)
    assert response.test_set_id is None
    assert response.results == results

# --- evaluated_output / output_source pass through on every detail ---


def test_standalone_details_carry_the_evaluated_output_and_its_source():
    test_id = uuid.uuid4()
    test_run_id = uuid.uuid4()
    row = _standalone_row(evaluated_output="Hello, Alice!", output_source=OutputSource.application)
    session = _standalone_session(test_id, test_run_id, row)

    response = asyncio.run(get_run_details_by_test_and_run_id(test_id, test_run_id, session))

    assert response.evaluated_output == "Hello, Alice!"
    assert response.output_source == OutputSource.application


def test_standalone_details_carry_the_applications_whole_reply():
    test_id = uuid.uuid4()
    test_run_id = uuid.uuid4()
    reply = {"output": "Hello, Alice!", "stop_reason": "end_turn", "input_tokens": 12}
    row = _standalone_row(evaluated_output="Hello, Alice!",
                          output_source=OutputSource.application, application_reply=reply)
    session = _standalone_session(test_id, test_run_id, row)

    response = asyncio.run(get_run_details_by_test_and_run_id(test_id, test_run_id, session))

    assert response.application_reply == reply


def test_set_run_details_carry_the_batch():
    test_set_id, execution_id, run_id, batch_id = (uuid.uuid4() for _ in range(4))
    session = AsyncMock()
    session.scalar.side_effect = [test_set_id, execution_id, execution_id, run_id, run_id]
    row = SimpleNamespace(
        status=TestStatus.pending, created_at=datetime.now().astimezone(),
        batch_id=batch_id, batch_index=7, test_set_entry_id=uuid.uuid4(), results=None,
        error=None, evaluated_output=None, output_source=None, application_reply=None,
        executed_at=None, test_id=uuid.uuid4(), name="n", input="i", expected_output=None,
        model_output="m", test_type_assignments=[], snapshot_at=datetime.now().astimezone(),
    )
    session.execute.return_value = MagicMock(one=MagicMock(return_value=row))

    response = asyncio.run(get_run_details_by_test_set_execution_and_run_id(
        test_set_id, execution_id, run_id, session))

    assert (response.batch_id, response.batch_index) == (batch_id, 7)


def test_plan_run_details_carry_the_batch():
    test_plan_id, execution_id, run_id, batch_id = (uuid.uuid4() for _ in range(4))
    session = AsyncMock()
    session.scalar.side_effect = [test_plan_id, execution_id, execution_id, run_id, run_id]
    row = SimpleNamespace(
        status=TestStatus.pending, created_at=datetime.now().astimezone(),
        batch_id=batch_id, batch_index=3, test_set_entry_id=uuid.uuid4(), results=None,
        error=None, evaluated_output=None, output_source=None, application_reply=None,
        executed_at=None, test_id=uuid.uuid4(), test_set_id=None, name="n", input="i",
        expected_output=None, model_output="m", test_type_assignments=[],
        snapshot_at=datetime.now().astimezone(),
    )
    session.execute.return_value = MagicMock(one=MagicMock(return_value=row))

    response = asyncio.run(get_run_details_by_test_plan_execution_and_run_id(
        test_plan_id, execution_id, run_id, session))

    assert (response.batch_id, response.batch_index) == (batch_id, 3)


def test_standalone_run_details_carry_the_batch():
    test_id, run_id, batch_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = AsyncMock()
    session.scalar.side_effect = [test_id, run_id, run_id]
    session.execute.return_value = MagicMock(
        one=MagicMock(return_value=_standalone_row(batch_id=batch_id, batch_index=12)))

    response = asyncio.run(get_run_details_by_test_and_run_id(test_id, run_id, session))

    assert (response.batch_id, response.batch_index) == (batch_id, 12)
