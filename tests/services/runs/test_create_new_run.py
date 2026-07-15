import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.models import TestModel, TestSetEntryModel, TestSetModel, TestTypeAssignmentModel
from assay.models.test import TestSetExecutionModel
from assay.services import (
    create_new_live_test_set_run,
    create_new_replay_test_set_run,
    create_new_standalone_run,
)

# --- create_new_standalone_run() ---

def test_standalone_test_not_found():
    test_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_standalone_run(test_id, session))

    session.scalars.assert_called_once()
    session.add.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert f"Tests with ids {[str(test_id)]} not found" in str(e.value.detail)
    

def test_standalone_empty_test_type_assignment():
    test_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[
        TestModel(
            id=test_id,
            name="Test Name",
            input="Test Input",
            expected_output="Test Expected Output",
            model_output="Test Model Output",
            test_type_assignments=[]
        ),
    ]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_standalone_run(test_id, session))

    session.scalars.assert_called_once()
    session.add.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert f"No test types assigned to Test with id {test_id}" in str(e.value.detail)
    
    
def test_standalone_happy_path():
    test_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[
        TestModel(
            id=test_id,
            name="Test Name",
            input="Test Input",
            expected_output="Test Expected Output",
            model_output="Test Model Output",
            test_type_assignments=[
                TestTypeAssignmentModel(
                    test_type_name="ROUGE",
                ),
            ],
        )
    ]))

    response = asyncio.run(create_new_standalone_run(test_id, session))

    test_run_model = session.add.call_args.args[0]

    session.scalars.assert_called_once()
    session.add.assert_called_once()
    session.commit.assert_called_once()
    assert response.id == test_run_model.id
    assert response.created_at == test_run_model.created_at
    assert response.status == test_run_model.status
    assert response.test_case_id.id == test_id


# --- create_new_live_test_set_run() ---

def test_new_live_test_set_run_test_set_not_found():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_live_test_set_run(test_set_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test set with ID '{test_set_id}' not found" in str(e.value.detail)


def test_new_live_test_set_run_test_set_entries_not_found():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = TestSetModel(id=test_set_id)
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_live_test_set_run(test_set_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert f"No Test Set Entries found in Test set with ID '{test_set_id}'" in str(e.value.detail)
    
    
def test_new_live_test_set_run_happy_path():
    test_set_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalar.return_value = TestSetModel(id=test_set_id)

    available_test_set_entry_models = [
        TestSetEntryModel(
            id=uuid.uuid4(),
        )
        for _ in range(0, 3)
    ]
    session.scalars.return_value = MagicMock(all=MagicMock(
        return_value=[entry.id for entry in available_test_set_entry_models]))

    response = asyncio.run(create_new_live_test_set_run(test_set_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    session.add.assert_called_once()
    session.add_all.assert_called_once()
    session.commit.assert_called_once()

    test_set_execution_model = session.add.call_args.args[0]
    assert test_set_execution_model.test_set_id == test_set_id

    test_runs_models = session.add_all.call_args.args[0]
    assert len(test_runs_models) == len(available_test_set_entry_models)

    # Every entry must have gotten exactly one run, and every run must point
    # back at the same execution — a plain length check wouldn't catch a
    # run created against the wrong entry or a stray/duplicate execution ID.
    test_runs_models_by_test_set_entry_id = {
        test_run_model.test_set_entry_id: test_run_model
        for test_run_model in test_runs_models
    }
    for available_test_set_entry in available_test_set_entry_models:
        matching_run = test_runs_models_by_test_set_entry_id[available_test_set_entry.id]
        assert matching_run.test_set_execution_id == test_set_execution_model.id

    assert response.id == test_set_execution_model.id
    assert response.created_at == test_set_execution_model.created_at
    assert response.test_set_id.id == test_set_id
    assert response.run_count == len(available_test_set_entry_models)


# --- create_new_replay_test_set_run() ---

def test_new_replay_test_set_run_test_set_not_found():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_replay_test_set_run(test_set_id, test_set_execution_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test set with ID '{test_set_id}' not found" in str(e.value.detail)


def test_new_replay_test_set_run_execution_not_found():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [TestSetModel(id=test_set_id), None]

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_replay_test_set_run(test_set_id, test_set_execution_id, session))

    assert session.scalar.call_count == 2
    session.scalars.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert (f"Test set execution with ID '{test_set_execution_id}' does not exist"
            in str(e.value.detail))


def test_new_replay_test_set_run_execution_not_linked_to_test_set():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [TestSetModel(id=test_set_id), test_set_execution_id, None]

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_replay_test_set_run(test_set_id, test_set_execution_id, session))

    assert session.scalar.call_count == 3
    session.scalars.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert (f"Test set execution with ID '{test_set_execution_id}' not linked "
            f"to test set with ID '{test_set_id}'") in str(e.value.detail)


def test_new_replay_test_set_run_execution_entries_not_found():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        TestSetModel(id=test_set_id), test_set_execution_id, test_set_execution_id
    ]
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_replay_test_set_run(test_set_id, test_set_execution_id, session))

    assert session.scalar.call_count == 3
    session.scalars.assert_called_once()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert (f"Test set execution with ID '{test_set_execution_id}' has no "
            f"test set entries") in str(e.value.detail)


def test_new_replay_test_set_run_happy_path():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        TestSetModel(id=test_set_id), test_set_execution_id, test_set_execution_id
    ]

    available_test_set_entry_models = [
        TestSetEntryModel(
            id=uuid.uuid4(),
        )
        for _ in range(0, 3)
    ]
    session.scalars.return_value = MagicMock(all=MagicMock(
        return_value=[entry.id for entry in available_test_set_entry_models]))

    response = asyncio.run(
        create_new_replay_test_set_run(test_set_id, test_set_execution_id, session)
    )

    assert session.scalar.call_count == 3
    session.scalars.assert_called_once()
    session.add.assert_called_once()
    session.add_all.assert_called_once()
    session.commit.assert_called_once()

    test_set_execution_model = session.add.call_args.args[0]
    assert isinstance(test_set_execution_model, TestSetExecutionModel)
    assert test_set_execution_model.test_set_id == test_set_id
    assert test_set_execution_model.replayed_execution_id == test_set_execution_id
    # The new execution's own ID must differ from the one being replayed —
    # otherwise the new runs below would silently re-attach to the old
    # execution instead of the new one.
    assert test_set_execution_model.id != test_set_execution_id

    test_runs_models = session.add_all.call_args.args[0]
    assert len(test_runs_models) == len(available_test_set_entry_models)

    # Every replayed entry must have gotten exactly one new run, and every
    # new run must point back at the NEW execution, not the replayed one —
    # a plain length check wouldn't catch a run left pointing at the old
    # execution ID.
    test_runs_models_by_test_set_entry_id = {
        test_run_model.test_set_entry_id: test_run_model
        for test_run_model in test_runs_models
    }
    for available_test_set_entry in available_test_set_entry_models:
        matching_run = test_runs_models_by_test_set_entry_id[available_test_set_entry.id]
        assert matching_run.test_set_execution_id == test_set_execution_model.id

    assert response.id == test_set_execution_model.id
    assert response.created_at == test_set_execution_model.created_at
    assert response.test_set_id.id == test_set_id
    assert response.run_count == len(available_test_set_entry_models)
    assert response.replayed_execution_id.id == test_set_execution_id