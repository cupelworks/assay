import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.services.datasets.update_dataset_rows import update_dataset_rows_by_id


def _make_dataset(dataset_id: uuid.UUID = None, name: str = "My Dataset") -> MagicMock:
    dataset = MagicMock()
    dataset.id = dataset_id or uuid.uuid4()
    dataset.name = name
    return dataset


def _make_row(row_id: uuid.UUID, dataset: MagicMock) -> MagicMock:
    row = MagicMock()
    row.id = row_id
    row.dataset = dataset
    return row


def _make_change(
        row_id: uuid.UUID, prompt: str = "p", expected: str = "e", output: str = "o") -> MagicMock:
    change = MagicMock()
    change.id = row_id
    change.row_info.prompt = prompt
    change.row_info.expected_output = expected
    change.row_info.model_output = output
    return change


def test_update_dataset_rows_mutates_attributes():
    row_id = uuid.uuid4()
    dataset = _make_dataset()
    row = _make_row(row_id, dataset)
    change = _make_change(row_id, prompt="new prompt", expected="new expected", output="new output")
    session = AsyncMock()

    with patch("assay.services.datasets.update_dataset_rows._get_rows_or_404",
               new=AsyncMock(return_value=[row])):
        asyncio.run(update_dataset_rows_by_id([change], session))

    assert row.input == "new prompt"
    assert row.expected_output == "new expected"
    assert row.model_output == "new output"


def test_update_dataset_rows_commits():
    row_id = uuid.uuid4()
    dataset = _make_dataset()
    row = _make_row(row_id, dataset)
    change = _make_change(row_id)
    session = AsyncMock()

    with patch("assay.services.datasets.update_dataset_rows._get_rows_or_404",
               new=AsyncMock(return_value=[row])):
        asyncio.run(update_dataset_rows_by_id([change], session))

    session.commit.assert_called_once()


def test_update_dataset_rows_returns_correct_info():
    row_id_1 = uuid.uuid4()
    row_id_2 = uuid.uuid4()
    dataset = _make_dataset()
    rows = [_make_row(row_id_1, dataset), _make_row(row_id_2, dataset)]
    changes = [_make_change(row_id_1), _make_change(row_id_2)]
    session = AsyncMock()

    with patch("assay.services.datasets.update_dataset_rows._get_rows_or_404",
               new=AsyncMock(return_value=rows)):
        result = asyncio.run(update_dataset_rows_by_id(changes, session))

    assert result.dataset.id == dataset.id
    assert result.dataset.name == dataset.name
    assert result.updated.n == 2
    assert set(result.updated.ids) == {row_id_1, row_id_2}


def test_update_dataset_rows_missing_ids_raises_404():
    session = AsyncMock()
    change = _make_change(uuid.uuid4())

    with patch(
        "assay.services.datasets.update_dataset_rows._get_rows_or_404",
        new=AsyncMock(side_effect=HTTPException(status_code=404, detail="Row ids not found: []")),
    ), pytest.raises(HTTPException) as exc:
        asyncio.run(update_dataset_rows_by_id([change], session))

    assert exc.value.status_code == 404
