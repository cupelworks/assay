import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from assay.schemas import TestSetName
from assay.services import create_new_test_set


def test_create_test_set_happy_path():
    session = AsyncMock()
    session.scalar.return_value = None

    test_set_name = "Test Set Name"
    request = TestSetName(
        name=test_set_name,
    )

    response = asyncio.run(
        create_new_test_set(request, session))

    session.add.assert_called_once()
    session.commit.assert_called_once()
    assert response.name == test_set_name
    assert isinstance(response.id, uuid.UUID)


def test_create_test_raises_409_if_name_taken():
    session = AsyncMock()
    session.scalar.return_value = "Test Set Name"

    test_set_name = "Test Set Name"
    request = TestSetName(
        name=test_set_name,
    )

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_test_set(request, session))

    assert e.value.status_code == 409
    assert test_set_name in str(e.value.detail)
    session.add.assert_not_called()
    session.commit.assert_not_called()
