import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from assay.models import TestRunModel, TestStatus
from assay.schemas import StandaloneRunDetails, TestCaseID
from assay.services import get_run_details_by_test_and_run_id

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