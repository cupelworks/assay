import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.schemas import CreateTestCaseRequest
from assay.services import create_new_test
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
