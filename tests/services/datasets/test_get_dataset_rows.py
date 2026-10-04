import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.services.datasets.get_dataset_rows import get_dataset_rows_by_id
from tests.services.standing_fakes import neutral_standing


def _make_row(row_id: uuid.UUID = None) -> MagicMock:
    row = MagicMock()
    row.id = row_id or uuid.uuid4()
    row.input = "prompt"
    row.expected_output = "expected"
    row.model_output = "output"
    row.position = 1
    return row


def _make_session(total: int, rows: list) -> AsyncMock:
    session = AsyncMock()
    session.scalar.return_value = total
    scalars_result = MagicMock()
    scalars_result.__iter__ = MagicMock(return_value=iter(rows))
    session.scalars.return_value = scalars_result
    return session


def test_get_dataset_rows_returns_correct_items():
    dataset_id = uuid.uuid4()
    row1 = _make_row()
    row2 = _make_row()
    session = _make_session(total=2, rows=[row1, row2])

    with patch("assay.services.datasets.get_dataset_rows._get_dataset_or_404",
               new=AsyncMock(return_value=MagicMock())), neutral_standing({row1.id: 3}):
        result = asyncio.run(get_dataset_rows_by_id(dataset_id, session, offset=0, limit=10))

    assert len(result.items) == 2
    assert (result.items[0].number, result.items[0].test_count) == (1, 3)
    assert result.items[1].test_count == 0
    assert result.items[0].id == row1.id
    assert result.items[0].row_info.prompt == row1.input
    assert result.items[0].row_info.expected_output == row1.expected_output
    assert result.items[0].row_info.model_output == row1.model_output


def test_get_dataset_rows_returns_correct_total():
    dataset_id = uuid.uuid4()
    session = _make_session(total=99, rows=[])

    with patch("assay.services.datasets.get_dataset_rows._get_dataset_or_404",
               new=AsyncMock(return_value=MagicMock())):
        result = asyncio.run(get_dataset_rows_by_id(dataset_id, session, offset=0, limit=10))

    assert result.total == 99


def test_get_dataset_rows_returns_dataset_id_offset_and_limit():
    dataset_id = uuid.uuid4()
    session = _make_session(total=0, rows=[])

    with patch("assay.services.datasets.get_dataset_rows._get_dataset_or_404",
               new=AsyncMock(return_value=MagicMock())):
        result = asyncio.run(get_dataset_rows_by_id(dataset_id, session, offset=5, limit=20))

    assert result.id == dataset_id
    assert result.offset == 5
    assert result.limit == 20


def test_get_dataset_rows_empty():
    dataset_id = uuid.uuid4()
    session = _make_session(total=0, rows=[])

    with patch("assay.services.datasets.get_dataset_rows._get_dataset_or_404",
               new=AsyncMock(return_value=MagicMock())):
        result = asyncio.run(get_dataset_rows_by_id(dataset_id, session, offset=0, limit=10))

    assert result.items == []
    assert result.total == 0


def test_get_dataset_rows_missing_dataset_raises_404():
    dataset_id = uuid.uuid4()
    session = _make_session(total=0, rows=[])

    with patch(
        "assay.services.datasets.get_dataset_rows._get_dataset_or_404",
        new=AsyncMock(side_effect=HTTPException(status_code=404, detail="Dataset not found")),
    ), pytest.raises(HTTPException) as exc:
        asyncio.run(get_dataset_rows_by_id(dataset_id, session, offset=0, limit=10))

    assert exc.value.status_code == 404
