import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.services import delete_dataset_by_id

# --- delete_dataset_by_id ---

def test_delete_dataset_by_id_not_found():
    request = MagicMock()
    request.id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(delete_dataset_by_id(request, session))

    assert e.value.status_code == 404
    assert e.value.detail == f"Dataset with id {request.id} not found"


def test_delete_dataset_by_id_found():
    request = MagicMock()
    request.id = uuid.uuid4()

    session = AsyncMock()

    row1 = MagicMock()
    row1.id = uuid.uuid4()
    row2 = MagicMock()
    row2.id = uuid.uuid4()

    dataset = MagicMock()
    dataset.id = uuid.uuid4()
    dataset.name = "My Dataset"
    dataset.rows = [row1, row2]

    session.scalar.return_value = dataset

    deleted_info = asyncio.run(delete_dataset_by_id(request, session))

    session.delete.assert_called_once()
    session.commit.assert_called_once()
    assert deleted_info.dataset.id == dataset.id
    assert deleted_info.dataset.name == dataset.name
    assert deleted_info.deleted.n == 2
    assert deleted_info.deleted.ids == [row1.id, row2.id]
