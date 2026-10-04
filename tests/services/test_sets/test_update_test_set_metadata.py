import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.models import TestSetModel
from assay.schemas import ModifyTestSetMetadataRequest
from assay.services import update_test_set_metadata_by_id
from assay.timestamps import as_utc
from tests.services.standing_fakes import neutral_standing


@pytest.fixture(autouse=True)
def _neutral_standing():
    """How items stand is tested on a real database (tests/test_standing.py)."""
    with neutral_standing(default=4):
        yield

def test_update_test_set_not_found():
    test_set_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalar.return_value = None
    
    with pytest.raises(HTTPException) as e:
        asyncio.run(
            update_test_set_metadata_by_id(
                test_set_id, MagicMock(), session
            )
        )

    session.scalar.assert_called_once()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert str(test_set_id) in str(e.value.detail)


def test_update_test_set_name_conflict():
    test_set_id = uuid.uuid4()
    test_set_name = "New Test Set Name"
    request = ModifyTestSetMetadataRequest(name=test_set_name)

    session = AsyncMock()
    session.scalar.side_effect = [
        TestSetModel(
            id=test_set_id,
            name="Previous Test Set Name",
            created_at=datetime.now(),
        ),
        test_set_name
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(
            update_test_set_metadata_by_id(
                test_set_id, request, session
            )
        )

    session.commit.assert_not_called()
    assert session.scalar.call_count == 2
    assert e.value.status_code == 409
    assert str(test_set_name) in str(e.value.detail)


def test_update_test_set_name_none_is_noop():
    test_set_id = uuid.uuid4()
    test_set_name = "Current Test Set Name"
    created_at = datetime.now()
    request = ModifyTestSetMetadataRequest(name=None)

    session = AsyncMock()
    session.scalar.side_effect = [
        TestSetModel(
            id=test_set_id,
            name=test_set_name,
            created_at=created_at,
        ),
        4,
    ]

    response = asyncio.run(
        update_test_set_metadata_by_id(
            test_set_id, request, session
        )
    )

    session.commit.assert_called_once()
    assert session.scalar.call_count == 1
    assert response.id == test_set_id
    assert response.name == test_set_name
    assert response.created_at == as_utc(created_at)
    assert response.entry_count == 4


def test_update_test_set_name_unchanged_is_noop():
    test_set_id = uuid.uuid4()
    test_set_name = "New and Old Test Set Name"
    created_at = datetime.now()
    request = ModifyTestSetMetadataRequest(name=test_set_name)

    session = AsyncMock()
    session.scalar.side_effect = [
        TestSetModel(
            id=test_set_id,
            name=test_set_name,
            created_at=created_at,
        ),
        4,
    ]

    response = asyncio.run(
        update_test_set_metadata_by_id(
            test_set_id, request, session
        )
    )

    session.commit.assert_called_once()
    assert session.scalar.call_count == 1
    assert response.id == test_set_id
    assert response.name == test_set_name
    assert response.created_at == as_utc(created_at)
    assert response.entry_count == 4


def test_update_test_set_name_happy_path():
    test_set_id = uuid.uuid4()
    test_set_name = "New Test Set Name"
    created_at = datetime.now()
    request = ModifyTestSetMetadataRequest(name=test_set_name)

    session = AsyncMock()
    session.scalar.side_effect = [
        TestSetModel(
            id=test_set_id,
            name="Old Test Set Name",
            created_at=created_at,
        ),
        None,
        4,
    ]

    response = asyncio.run(
        update_test_set_metadata_by_id(
            test_set_id, request, session
        )
    )

    session.commit.assert_called_once()
    assert session.scalar.call_count == 2
    assert response.id == test_set_id
    assert response.name == test_set_name
    assert response.created_at == as_utc(created_at)
    assert response.entry_count == 4
