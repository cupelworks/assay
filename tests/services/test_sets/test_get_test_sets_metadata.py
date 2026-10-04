import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.models import TestSetModel
from assay.services import get_all_test_sets_metadata, get_test_set_metadata_by_id
from tests.services.standing_fakes import neutral_standing

# --- get_all_test_sets_metadata() ---


@pytest.fixture(autouse=True)
def _neutral_standing():
    """How items stand is tested on a real database (tests/test_standing.py)."""
    with neutral_standing({}, default=7):
        yield

def test_returns_correct_items():
    offset = 1
    limit = 2
    first_created_at = datetime(2025, 1, 1)
    second_created_at = datetime(2026, 1, 1)
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    first_name = "Test Set 2"
    second_name = "Test Set 3"

    session = AsyncMock()
    session.scalar.return_value = 3
    session.scalars.return_value = MagicMock(all=MagicMock(
        return_value=[
            TestSetModel(
                id=first_id,
                name=first_name,
                created_at=first_created_at,
            ),
            TestSetModel(
                id=second_id,
                name=second_name,
                created_at=second_created_at,
            ),
        ]
    ))

    with neutral_standing({first_id: 5, second_id: 0}):
        response = asyncio.run(get_all_test_sets_metadata(session, offset=offset, limit=limit))

    assert response.total == 3
    assert response.offset == offset
    assert response.limit == limit
    assert len(response.items) == 2
    assert response.items[0].id == first_id
    assert response.items[0].name == first_name
    assert response.items[0].created_at == first_created_at
    assert response.items[0].entry_count == 5
    assert response.items[1].id == second_id
    assert response.items[1].name == second_name
    assert response.items[1].created_at == second_created_at
    assert response.items[1].entry_count == 0


def test_empty_table():
    session = AsyncMock()
    session.scalar.return_value = 0
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    session.execute.return_value = MagicMock()
    session.execute.return_value.tuples.return_value.all.return_value = []

    response = asyncio.run(get_all_test_sets_metadata(session))

    assert response.total == 0
    assert len(response.items) == 0


# --- get_test_set_metadata_by_id() ---

def test_metadata_correctly_returned():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        TestSetModel(
            id=test_set_id,
            name="Test Set",
            created_at=datetime(2026, 1, 1),
        ),
        7,
    ]

    response = asyncio.run(get_test_set_metadata_by_id(test_set_id, session))

    assert response.id == test_set_id
    assert response.name == "Test Set"
    assert response.created_at == datetime(2026, 1, 1)
    assert response.entry_count == 7


def test_test_set_not_found():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_test_set_metadata_by_id(test_set_id, session))

    assert e.value.status_code == 404
    assert str(test_set_id) in str(e.value.detail)
