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
    mock_assignment.label = "ROUGE"
    mock_assignment.config = None
    mock_assignment.answer_path = None
    mock_test.test_type_assignments = [mock_assignment]
    mock_request = ModifyTestCaseRequest()  # noqa
    session = _get_session(mock_test)

    with patch("assay.services.tests.update_test._validate_test_type_assignments"):
        response = _call_api_orchestrator(mock_test_id, mock_request, session)

    session.commit.assert_called_once()
    assert response.test_type_assignments == [
        TestTypeAssignment(name="ROUGE", label="ROUGE", config=None)]


def test_modify_raises_422_when_clearing_expected_output_with_reference_required_type_assigned():
    mock_test_id = uuid.uuid4()

    mock_test = _get_mock_test_without_test_type_assignments(mock_test_id)
    mock_assignment = MagicMock()
    mock_assignment.test_type_name = "Exact Match"
    mock_assignment.label = "Exact Match"
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
    assert response.test_type_assignments == [
        TestTypeAssignment(name="Exact Match", label="Exact Match", config=None)]


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
    mock_assignment.label = "Exact Match"
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


# -- labels: replacing the list with a type assigned more than once --


def _stored(type_name, label, config):
    assignment = MagicMock()
    assignment.test_type_name, assignment.label = type_name, label
    assignment.config, assignment.answer_path = config, None
    return assignment


def _patch_assignments(mock_test, assignments):
    session = _get_session(mock_test)
    with patch("assay.services.tests.update_test._validate_test_type_assignments"):
        return _call_api_orchestrator(
            mock_test.id, ModifyTestCaseRequest(test_type_assignments=assignments), session)


def _saved(mock_test):
    return [(a.test_type_name, a.label, a.config) for a in mock_test.test_type_assignments]


def test_modify_saves_the_same_type_twice_instead_of_keeping_only_the_last():
    mock_test = _get_mock_test_without_test_type_assignments(uuid.uuid4())

    response = _patch_assignments(mock_test, [
        TestTypeAssignment(name="Contains", config={"substring": "refund"}),
        TestTypeAssignment(name="Contains", config={"substring": "4471"}),
    ])

    assert _saved(mock_test) == [("Contains", "Contains", {"substring": "refund"}),
                                 ("Contains", "Contains 2", {"substring": "4471"})]
    assert [a.label for a in response.test_type_assignments] == ["Contains", "Contains 2"]


def test_modify_keeps_the_seconds_label_when_the_first_of_two_is_removed():
    mock_test = _get_mock_test_without_test_type_assignments(uuid.uuid4())
    mock_test.test_type_assignments = [
        _stored("Contains", "Contains", {"substring": "refund"}),
        _stored("Contains", "Contains 2", {"substring": "4471"}),
    ]

    _patch_assignments(mock_test, [
        TestTypeAssignment(name="Contains", label="Contains 2", config={"substring": "4471"}),
    ])

    assert _saved(mock_test) == [("Contains", "Contains 2", {"substring": "4471"})]


def test_modify_gives_a_new_check_the_next_free_number_and_keeps_the_sent_labels():
    mock_test = _get_mock_test_without_test_type_assignments(uuid.uuid4())

    _patch_assignments(mock_test, [
        TestTypeAssignment(name="Contains", label="Contains 2", config={"substring": "4471"}),
        TestTypeAssignment(name="Contains", config={"substring": "refund"}),
        TestTypeAssignment(name="Contains", config={"substring": "today"}),
    ])

    assert [label for _, label, _ in _saved(mock_test)] == ["Contains 2", "Contains",
                                                           "Contains 3"]


def test_modify_null_and_empty_still_mean_untouched_and_remove_all_with_labels():
    stored = [_stored("Contains", "Refund", {"substring": "refund"}),
              _stored("Contains", "Contains 2", {"substring": "4471"})]
    mock_test = _get_mock_test_without_test_type_assignments(uuid.uuid4())
    mock_test.test_type_assignments = list(stored)

    response = _patch_assignments(mock_test, None)
    assert mock_test.test_type_assignments == stored
    assert [a.label for a in response.test_type_assignments] == ["Refund", "Contains 2"]

    _patch_assignments(mock_test, [])
    assert mock_test.test_type_assignments == []


def test_modify_with_duplicate_labels_is_a_422_and_changes_nothing():
    stored = [_stored("Contains", "Contains", {"substring": "refund"})]
    mock_test = _get_mock_test_without_test_type_assignments(uuid.uuid4())
    mock_test.test_type_assignments = list(stored)
    session = _get_session(mock_test)
    catalogue_row = MagicMock()
    catalogue_row.name = "Contains"
    catalogue_row.config_fields = [{"key": "substring", "label": "Substring",
                                    "kind": "multiline", "required": True}]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[catalogue_row]))

    with pytest.raises(HTTPException) as exc:
        _call_api_orchestrator(mock_test.id, ModifyTestCaseRequest(test_type_assignments=[
            TestTypeAssignment(name="Contains", label="Refund", config={"substring": "a"}),
            TestTypeAssignment(name="Contains", label="refund", config={"substring": "b"}),
        ]), session)

    assert exc.value.status_code == 422
    assert "Duplicate labels" in exc.value.detail
    assert mock_test.test_type_assignments == stored
    session.commit.assert_not_called()
