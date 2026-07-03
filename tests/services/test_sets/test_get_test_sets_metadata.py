import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from assay.models import TestSetModel
from assay.services import get_all_test_sets_metadata

# --- get_all_test_sets_metadata() ---

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

    response = asyncio.run(get_all_test_sets_metadata(session, offset, limit))

    assert response.total == 3
    assert response.offset == offset
    assert response.limit == limit
    assert len(response.items) == 2
    assert response.items[0].id == first_id
    assert response.items[0].name == first_name
    assert response.items[0].created_at == first_created_at
    assert response.items[1].id == second_id
    assert response.items[1].name == second_name
    assert response.items[1].created_at == second_created_at


def test_empty_table():
    session = AsyncMock()
    session.scalar.return_value = 0
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    response = asyncio.run(get_all_test_sets_metadata(session))

    assert response.total == 0
    assert len(response.items) == 0
