import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.services.datasets.delete_dataset_rows import (
    _build_deleted_rows_info,
    delete_dataset_rows_by_ids,
)

# --- delete_dataset_rows_by_ids ---

def _make_row(row_id: uuid.UUID, dataset: MagicMock) -> MagicMock:
    row = MagicMock()
    row.id = row_id
    row.dataset = dataset
    row.dataset_id = dataset.id
    return row


def _make_session(rows: list) -> AsyncMock:
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = rows
    session.scalars.return_value = scalars_result
    return session


def test_delete_dataset_rows_missing_ids_raises_404():
    existing_id = uuid.uuid4()
    missing_id = uuid.uuid4()

    request = MagicMock()
    request.row_ids = [existing_id, missing_id]

    dataset = MagicMock()
    dataset.id = uuid.uuid4()
    dataset.name = "My Dataset"

    row = _make_row(existing_id, dataset)
    session = _make_session([row])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(delete_dataset_rows_by_ids(request, session))

    assert exc.value.status_code == 404
    assert str(missing_id) in exc.value.detail


def test_delete_dataset_rows_all_missing_raises_404():
    request = MagicMock()
    request.row_ids = [uuid.uuid4(), uuid.uuid4()]

    session = _make_session([])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(delete_dataset_rows_by_ids(request, session))

    assert exc.value.status_code == 404


def test_delete_dataset_rows_commits_and_executes():
    row_id = uuid.uuid4()

    request = MagicMock()
    request.row_ids = [row_id]

    dataset = MagicMock()
    dataset.id = uuid.uuid4()
    dataset.name = "My Dataset"

    row = _make_row(row_id, dataset)
    session = _make_session([row])

    asyncio.run(delete_dataset_rows_by_ids(request, session))

    session.execute.assert_called_once()
    session.commit.assert_called_once()


def test_delete_dataset_rows_returns_correct_info():
    row_id_1 = uuid.uuid4()
    row_id_2 = uuid.uuid4()

    request = MagicMock()
    request.row_ids = [row_id_1, row_id_2]

    dataset = MagicMock()
    dataset.id = uuid.uuid4()
    dataset.name = "My Dataset"

    rows = [_make_row(row_id_1, dataset), _make_row(row_id_2, dataset)]
    session = _make_session(rows)

    result = asyncio.run(delete_dataset_rows_by_ids(request, session))

    assert result.dataset.id == dataset.id
    assert result.dataset.name == dataset.name
    assert result.deleted.n == 2
    assert result.deleted.ids == [row_id_1, row_id_2]


# --- _build_deleted_rows_info ---

def test_build_deleted_rows_info():
    dataset = MagicMock()
    dataset.id = uuid.uuid4()
    dataset.name = "My Dataset"

    row1 = _make_row(uuid.uuid4(), dataset)
    row2 = _make_row(uuid.uuid4(), dataset)

    result = _build_deleted_rows_info([row1, row2])

    assert result.dataset.id == dataset.id
    assert result.dataset.name == dataset.name
    assert result.deleted.n == 2
    assert result.deleted.ids == [row1.id, row2.id]
