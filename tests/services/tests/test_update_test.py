import asyncio
import logging
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
    mock_assignment.answer_path = None
    mock_test.test_type_assignments = [mock_assignment]
    mock_request = ModifyTestCaseRequest()  # noqa
    session = _get_session(mock_test)

    with patch("assay.services.tests.update_test._validate_test_type_assignments"):
        response = _call_api_orchestrator(mock_test_id, mock_request, session)

    session.commit.assert_called_once()
    assert response.test_type_assignments == [TestTypeAssignment(name="ROUGE", config=None)]


def test_modify_raises_422_when_clearing_expected_output_with_reference_required_type_assigned():
    mock_test_id = uuid.uuid4()

    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_assignment = MagicMock()
    mock_assignment.test_type_name = "Exact Match"
    mock_assignment.config = None
    mock_assignment.answer_path = None
    mock_test.test_type_assignments = [mock_assignment]
    # this request only clears expected_output — test_type_assignments isn't
    # touched at all, so the *existing* Exact Match assignment stays in
    # effect and must still be caught, not silently skipped because this
    # particular request doesn't mention assignments
    mock_request = ModifyTestCaseRequest(expected_output="") # noqa
    session = _get_session(mock_test)
    catalogue_row = MagicMock()
    catalogue_row.name = "Exact Match"
    catalogue_row.config_fields = [
        {"key": "reference", "label": "Expected output", "kind": "reference", "required": True}
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[catalogue_row]))

    with pytest.raises(HTTPException) as e:
        _call_api_orchestrator(mock_test_id, mock_request, session)

    session.commit.assert_not_called()
    assert e.value.status_code == 422
    assert "Exact Match" in str(e.value.detail)


def test_modify_passes_assigning_reference_required_type_with_existing_expected_output():
    mock_test_id = uuid.uuid4()

    # mock_test.expected_output is already non-empty ("Testing Expected
    # Output") and this request doesn't touch it — only assignments change
    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_request = ModifyTestCaseRequest(
        test_type_assignments=[TestTypeAssignment(name="Exact Match")],
    )  # noqa
    session = _get_session(mock_test)
    catalogue_row = MagicMock()
    catalogue_row.name = "Exact Match"
    catalogue_row.config_fields = [
        {"key": "reference", "label": "Expected output", "kind": "reference", "required": True}
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[catalogue_row]))

    response = _call_api_orchestrator(mock_test_id, mock_request, session)

    session.commit.assert_called_once()
    assert response.test_type_assignments == [TestTypeAssignment(name="Exact Match", config=None)]


# -- null clears a nullable field, a missing key keeps it --


def test_modify_null_model_output_clears_it():
    mock_test_id = uuid.uuid4()
    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    # model_validate on a dict, as FastAPI builds it from the JSON body
    mock_request = ModifyTestCaseRequest.model_validate({"model_output": None})

    response = _call_api_orchestrator(mock_test_id, mock_request, _get_session(mock_test))

    assert response.model_output is None
    assert response.expected_output == "Testing Expected Output"


def test_modify_null_expected_output_clears_it():
    mock_test_id = uuid.uuid4()
    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_request = ModifyTestCaseRequest.model_validate({"expected_output": None})

    response = _call_api_orchestrator(mock_test_id, mock_request, _get_session(mock_test))

    assert response.expected_output is None
    assert response.model_output == "Testing Model Output"


def test_modify_leaving_the_outputs_out_keeps_them():
    mock_test_id = uuid.uuid4()
    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_request = ModifyTestCaseRequest.model_validate({"input": "New input"})

    response = _call_api_orchestrator(mock_test_id, mock_request, _get_session(mock_test))

    assert response.input == "New input"
    assert response.model_output == "Testing Model Output"
    assert response.expected_output == "Testing Expected Output"


def test_modify_null_name_or_input_changes_nothing():
    mock_test_id = uuid.uuid4()
    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_request = ModifyTestCaseRequest.model_validate({"name": None, "input": None})

    response = _call_api_orchestrator(mock_test_id, mock_request, _get_session(mock_test))

    assert (response.name, response.input) == ("Testing Name", "Testing Input")


def test_modify_raises_422_when_nulling_expected_output_with_reference_required_type_assigned():
    mock_test_id = uuid.uuid4()
    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_assignment = MagicMock()
    mock_assignment.test_type_name = "Exact Match"
    mock_assignment.config = None
    mock_assignment.answer_path = None
    mock_test.test_type_assignments = [mock_assignment]
    mock_request = ModifyTestCaseRequest.model_validate({"expected_output": None})
    session = _get_session(mock_test)
    catalogue_row = MagicMock()
    catalogue_row.name = "Exact Match"
    catalogue_row.config_fields = [
        {"key": "reference", "label": "Expected output", "kind": "reference", "required": True}
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[catalogue_row]))

    with pytest.raises(HTTPException) as e:
        _call_api_orchestrator(mock_test_id, mock_request, session)

    # the check sees the value the request is about to write, null included
    session.commit.assert_not_called()
    assert e.value.status_code == 422
    assert mock_test.expected_output == "Testing Expected Output"


def test_modify_logs_a_cleared_field_but_not_an_ignored_null(caplog):
    mock_test_id = uuid.uuid4()
    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_request = ModifyTestCaseRequest.model_validate({"model_output": None, "name": None})

    with caplog.at_level(logging.INFO, logger="assay.services.tests.update_test"):
        _call_api_orchestrator(mock_test_id, mock_request, _get_session(mock_test))

    (record,) = [r for r in caplog.records if r.name == "assay.services.tests.update_test"]
    assert record.fields == ["model_output"]
