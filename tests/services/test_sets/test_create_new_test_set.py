import asyncio
import logging
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


def test_create_test_set_logs_the_creation_with_its_id(caplog):
    session = AsyncMock()
    session.scalar.return_value = None

    with caplog.at_level(logging.INFO, logger="assay.services.test_sets"):
        response = asyncio.run(create_new_test_set(TestSetName(name="Regression"), session))

    record = next(r for r in caplog.records if r.name == "assay.services.test_sets.create_test_set")
    assert record.getMessage() == f"Created test set {response.id} ('Regression')"
    assert record.test_set_id == response.id


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
