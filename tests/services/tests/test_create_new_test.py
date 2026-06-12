import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest

from assay.schemas import CreateTestCaseRequest
from assay.services import create_new_test

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
