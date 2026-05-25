import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.services import (
    get_dataset_metadata_by_id,
    get_datasets_metadata,
)


def _make_dataset(dataset_id: uuid.UUID = None, name: str = "My Dataset") -> MagicMock:
    dataset = MagicMock()
    dataset.id = dataset_id or uuid.uuid4()
    dataset.name = name
    dataset.created_at = datetime(2024, 1, 1)
    return dataset


def _make_session(total: int, datasets: list) -> AsyncMock:
    session = AsyncMock()
    session.scalar.return_value = total
    scalars_result = MagicMock()
    scalars_result.__iter__ = MagicMock(return_value=iter(datasets))
    session.scalars.return_value = scalars_result
    return session


def test_get_datasets_metadata_returns_correct_items():
    ds1 = _make_dataset(name="Dataset A")
    ds2 = _make_dataset(name="Dataset B")
    session = _make_session(total=2, datasets=[ds1, ds2])

    result = asyncio.run(get_datasets_metadata(offset=0, limit=10, session=session))

    assert len(result.items) == 2
    assert result.items[0].id == ds1.id
    assert result.items[0].name == ds1.name
    assert result.items[0].created_at == ds1.created_at
    assert result.items[1].id == ds2.id


def test_get_datasets_metadata_returns_correct_total():
    session = _make_session(total=42, datasets=[])

    result = asyncio.run(get_datasets_metadata(offset=0, limit=10, session=session))

    assert result.total == 42


def test_get_datasets_metadata_returns_offset_and_limit():
    session = _make_session(total=0, datasets=[])

    result = asyncio.run(get_datasets_metadata(offset=20, limit=5, session=session))

    assert result.offset == 20
    assert result.limit == 5


def test_get_datasets_metadata_empty():
    session = _make_session(total=0, datasets=[])

    result = asyncio.run(get_datasets_metadata(offset=0, limit=10, session=session))

    assert result.items == []
    assert result.total == 0


# --- get_dataset_metadata_by_id ---

def test_get_dataset_metadata_by_id_returns_correct_metadata():
    dataset = _make_dataset()
    session = AsyncMock()

    with patch("assay.services.datasets.get_datasets_metadata._get_dataset_or_404",
               new=AsyncMock(return_value=dataset)):
        result = asyncio.run(get_dataset_metadata_by_id(dataset.id, session))

    assert result.id == dataset.id
    assert result.name == dataset.name
    assert result.created_at == dataset.created_at


def test_get_dataset_metadata_by_id_missing_raises_404():
    session = AsyncMock()

    with patch(
        "assay.services.datasets.get_datasets_metadata._get_dataset_or_404",
        new=AsyncMock(side_effect=HTTPException(status_code=404, detail="Dataset not found")),
    ), pytest.raises(HTTPException) as exc:
        asyncio.run(get_dataset_metadata_by_id(uuid.uuid4(), session))

    assert exc.value.status_code == 404
