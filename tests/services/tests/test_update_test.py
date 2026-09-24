import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.schemas import ModifyTestCaseRequest, TestTypeAssignment
from assay.services import modify_test_by_id


def _get_mock_test_without_test_type_assignments(
        mock_test_id: uuid.UUID):
    mock_test = MagicMock()
    mock_test.id = mock_test_id
    mock_test.name = "Testing Name"
    mock_test.input = "Testing Input"
    mock_test.model_output = "Testing Model Output"
    mock_test.expected_output = "Testing Expected Output"
    mock_test.test_type_assignments = []
    return mock_test


def _get_session(mock_test):
    session = AsyncMock()
    session.scalar.return_value = mock_test
    return session


def _call_api_orchestrator(mock_test_id, mock_request, session):
    return asyncio.run(
        modify_test_by_id(mock_test_id, mock_request, session)
    )


def test_modify_name_only():
    mock_test_id = uuid.uuid4()

    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_request = ModifyTestCaseRequest(name="Changed Name") # noqa
    session = _get_session(mock_test)
    response = _call_api_orchestrator(mock_test_id, mock_request, session)

    session.commit.assert_called_once()
    assert response.name == mock_request.name
    assert response.input == mock_test.input
    assert response.model_output == mock_test.model_output
    assert response.expected_output == mock_test.expected_output
    assert response.test_type_assignments == []


def test_modify_multiple_fields():
    mock_test_id = uuid.uuid4()

    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_request = ModifyTestCaseRequest(
        input="Testing Input", expected_output="Testing",
        model_output="Testing model") # noqa
    session = _get_session(mock_test)
    response = _call_api_orchestrator(mock_test_id, mock_request, session)

    session.commit.assert_called_once()
    assert response.name == mock_test.name
    assert response.input == mock_request.input
    assert response.model_output == mock_request.model_output
    assert response.expected_output == mock_request.expected_output
    assert response.test_type_assignments == []


def test_modify_raises_404_if_test_not_found():
    mock_test_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        _call_api_orchestrator(mock_test_id, MagicMock(), session)

    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert str(mock_test_id) in str(e.value.detail)


def test_modify_raises_422_for_unknown_test_type():
    mock_test_id = uuid.uuid4()

    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_request = ModifyTestCaseRequest(
        test_type_assignments=[
            TestTypeAssignment(name="Testing Name"),
            TestTypeAssignment(name="ROUGE"),
        ],
    ) # noqa
    session = _get_session(mock_test)
    catalogue_row = MagicMock()
    catalogue_row.name = "ROUGE"
    catalogue_row.config_fields = [
        {"key": "reference", "label": "Reference text", "kind": "reference", "required": True}
    ]
    session.execute.return_value = MagicMock(
        all=MagicMock(return_value=[catalogue_row])
    )

    with pytest.raises(HTTPException) as e:
        _call_api_orchestrator(mock_test_id, mock_request, session)

    session.commit.assert_not_called()
    assert e.value.status_code == 422
    assert "Testing Name" in str(e.value.detail)


def test_modify_test_type_assignments_empty_removes_all_assignments():
    mock_test_id = uuid.uuid4()

    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_test.test_type_assignments = ["ROUGE"]
    mock_request = ModifyTestCaseRequest(
        test_type_assignments=[],
    ) # noqa
    session = _get_session(mock_test)

    with patch(
            "assay.services.tests.update_test._validate_test_type_assignments"):
        response = _call_api_orchestrator(mock_test_id, mock_request, session)

    session.commit.assert_called_once()
    assert response.test_type_assignments == []


def test_modify_test_type_assignments_none_leaves_assignments_untouched():
    mock_test_id = uuid.uuid4()

    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_assignment = MagicMock()
    mock_assignment.test_type_name = "ROUGE"
    mock_assignment.config = None
    mock_test.test_type_assignments = [mock_assignment]
    mock_request = ModifyTestCaseRequest()  # noqa
    session = _get_session(mock_test)

    with patch("assay.services.tests.update_test._validate_test_type_assignments"):
        response = _call_api_orchestrator(mock_test_id, mock_request, session)

    session.commit.assert_called_once()
    assert response.test_type_assignments == [TestTypeAssignment(name="ROUGE", config=None)]
