import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.models import TestModel, TestSetEntryModel, TestSetModel, TestTypeAssignmentModel
from assay.services import create_new_live_test_set_run, create_new_standalone_run

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