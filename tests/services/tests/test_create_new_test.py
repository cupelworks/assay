import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException

from assay.schemas import CreateTestCaseRequest
from assay.services import create_new_test, create_new_test_from_dataset
from assay.services.tests.create_new_test import _validate_test_type_name

name = "Test Name"
model_input = "My Input"
model_output = "The model output"
expected_output = "The expected model output"

def test_create_new_test_without_name():
    request = CreateTestCaseRequest(
        input=model_input,
        model_output=model_output,
        expected_output=expected_output
    )
    uuid.UUID(request.name)
    
    mock_session = AsyncMock()

    response = asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_called_once()
    uuid.UUID(str(response.id))
    uuid.UUID(response.name)
    assert response.input == model_input
    assert response.model_output == model_output
    assert response.expected_output == expected_output


def test_create_new_test_with_name():
    request = CreateTestCaseRequest(
        name=name,
        input=model_input,
        model_output=model_output,
        expected_output=expected_output
    )
    with pytest.raises(ValueError):
        uuid.UUID(request.name)

    mock_session = AsyncMock()

    response = asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_called_once()
    assert response.name == name


def test_create_new_test_without_model_expected_output():
    request = CreateTestCaseRequest(
        name=name,
        input=model_input,
        model_output=None,
        expected_output=None
    )

    mock_session = AsyncMock()

    response = asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_called_once()
    assert response.model_output is None
    assert response.expected_output is None
    
    
def test_create_new_test_with_test_names():
    request = CreateTestCaseRequest(
        input=model_input,
        model_output=model_output,
        expected_output=expected_output,
        test_type_names=["ROUGE", "BERTScore"],
    )

    mock_session = AsyncMock()

    with patch(
            "assay.services.tests.create_new_test._validate_test_type_name",
            new=AsyncMock()
    ):
        response = asyncio.run(create_new_test(request, mock_session))

    mock_session.commit.assert_called_once()
    assert response.test_type_names == ["ROUGE", "BERTScore"]


def test_validate_raises_for_unknown_name():
    mock_session = AsyncMock()
    mock_session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_validate_test_type_name(mock_session, ["ROUGE"]))
    assert exc.value.status_code == 422


def test_validate_passes_for_known_name():
    mock_session = AsyncMock()
    mock_session.scalars.return_value = MagicMock(all=MagicMock(return_value=["ROUGE"]))

    asyncio.run(_validate_test_type_name(mock_session, ["ROUGE"]))  # no raise


# -- create_new_test_from_dataset

def _get_mock_request_with_id():
    # returns both the mock and the raw dataset id string
    mock_request_id = str(uuid.uuid4())
    return MagicMock(id=mock_request_id), mock_request_id


def _patch_rows(*rows):
    # shorthand for mocking the DB call that returns dataset rows
    return patch(
        "assay.services.tests.create_new_test._get_all_rows_or_404",
        new=AsyncMock(return_value=list(rows)),
    )


def _patch_validate_test_type_name():
    return patch("assay.services.tests.create_new_test._validate_test_type_name")


def _get_rows():
    return MagicMock(
        input="first", expected_output="y", model_output="z", id=uuid.uuid4()), MagicMock(
        input="second", expected_output="y", model_output="z", id=uuid.uuid4()), MagicMock(
        input="third", expected_output="y", model_output="z", id=uuid.uuid4()),

def test_row_exist_no_test_type_names():
    mock_request, mock_request_id = _get_mock_request_with_id()
    # [] is falsy, so the `if request.test_type_names:` branch is skipped entirely
    mock_request.test_type_names = []

    mock_session = AsyncMock()

    # _patch_validate_test_type_name is captured so we can assert it was never called
    with _patch_rows(*_get_rows()), \
            _patch_validate_test_type_name() as mock_validate_test_type_name:
        result = asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    # validation must be skipped when no test type names are provided
    mock_validate_test_type_name.assert_not_called()
    mock_session.commit.assert_called_once()
    # the response must carry back the original dataset id
    assert result.id == UUID(mock_request_id)
    # 3 rows in → 3 TestModels created → 3 ids in the response
    assert len(result.test_cases) == 3
    # add_all is called twice: first for tests, then for test_types.
    # [1] = second call, [0] = positional args tuple, [0] = the list passed in.
    # with no test_type_names, no TestTypeAssignmentModels should be created.
    assert mock_session.add_all.call_args_list[1][0][0] == []


def test_correct_number_of_test_type_assignments():
    mock_request, mock_request_id = _get_mock_request_with_id()
    # 2 test types will be assigned to each test
    mock_request.test_type_names = ["ROUGE", "BERTScore"]

    mock_session = AsyncMock()

    # 3 rows → 3 TestModels, each assigned 2 test types → 6 TestTypeAssignmentModels
    with _patch_rows(*_get_rows()), _patch_validate_test_type_name():
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    # first add_all call is for tests: one per row
    assert len(mock_session.add_all.call_args_list[0][0][0]) == 3
    # second add_all call is for test_types: cartesian product of tests × type names (3 × 2)
    assert len(mock_session.add_all.call_args_list[1][0][0]) == 6


def test_correct_mapping():
    mock_request, mock_request_id = _get_mock_request_with_id()

    mock_session = AsyncMock()

    with _patch_rows(*_get_rows()), _patch_validate_test_type_name():
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    # mapping is the same for every row, so checking one is enough.
    # [0] = first add_all call (tests), [0] = positional args, [0] = the list, [0] = first TestModel
    single_test = mock_session.add_all.call_args_list[0][0][0][0]
    assert single_test.input == "first"
    assert single_test.expected_output == "y"
    assert single_test.model_output == "z"


def test_database_not_found():
    mock_session = AsyncMock()
    mock_request, _ = _get_mock_request_with_id()

    # force _get_dataset_or_404 to raise 404 via side_effect — execution stops there,
    # the session and all subsequent steps are never reached
    with patch(
            "assay.services.tests.create_new_test._get_dataset_or_404",
            new=AsyncMock(side_effect=HTTPException(status_code=404)),
    ), \
            pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    assert exc.value.status_code == 404


def test_no_dataset_rows():
    mock_session = AsyncMock()
    # make session.scalars(...).all() return [] so the real _get_all_rows_or_404 raises 404
    mock_session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    mock_request, _ = _get_mock_request_with_id()

    # safety net: _get_dataset_or_404 would pass anyway since AsyncMock is always truthy,
    # but patching makes the intent explicit and avoids relying on that side effect
    with patch("assay.services.tests.create_new_test._get_dataset_or_404"), \
            pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    assert exc.value.status_code == 404


def test_unknown_test_type_name():
    mock_session = AsyncMock()
    # simulate the test types catalogue returning no matches — _validate_test_type_name
    # computes the difference between requested names and found names, and raises 422 if non-empty
    mock_session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    mock_request, _ = _get_mock_request_with_id()
    mock_request.test_type_names = ["SHOULD FAIL"]

    # _get_dataset_or_404 is patched as a safety net (AsyncMock is always truthy)
    # _patch_rows lets execution reach _validate_test_type_name, which is where the 422 is raised
    with patch("assay.services.tests.create_new_test._get_dataset_or_404"), \
            _patch_rows(*_get_rows()), \
            pytest.raises(HTTPException) as exc:
        asyncio.run(create_new_test_from_dataset(mock_request, mock_session))

    assert exc.value.status_code == 422





