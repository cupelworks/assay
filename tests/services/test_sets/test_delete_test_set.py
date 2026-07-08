import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from assay.models import TestSetModel
from assay.services import delete_test_set_by_id

# --- delete_test_set_by_id() ---

def test_test_set_not_found():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(delete_test_set_by_id(test_set_id, session))

    session.delete.assert_not_called()
    session.commit.assert_not_called()
    assert session.scalar.call_count == 1
    assert e.value.status_code == 404
    assert str(test_set_id) in str(e.value.detail)


def test_delete_blocked_when_entry_has_runs():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = uuid.uuid4()

    with patch("assay.services.test_sets.delete_test_set._find_test_set_or_404"), \
        pytest.raises(HTTPException) as e:
        asyncio.run(delete_test_set_by_id(test_set_id, session))

    session.delete.assert_not_called()
    session.commit.assert_not_called()
    assert session.scalar.call_count == 1
    assert e.value.status_code == 409
    assert str(test_set_id) in str(e.value.detail)


def test_test_set_deleted():
    test_set_id = uuid.uuid4()

    test_set_to_delete = TestSetModel(
        id=test_set_id,
        name="Test Set Name",
        created_at=datetime.now().astimezone(),
    )

    session = AsyncMock()
    session.scalar.side_effect = [test_set_to_delete, None]

    asyncio.run(delete_test_set_by_id(test_set_id, session))

    session.delete.assert_called_once_with(test_set_to_delete)
    session.commit.assert_called_once()
    assert session.scalar.call_count == 2
