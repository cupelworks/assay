import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.models import TestModel, TestTypeAssignmentModel
from assay.services import create_new_standalone_run

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
    assert f"Tests with ids {[str(test_id)]} not found"
    

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
