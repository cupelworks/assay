import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import Delete

from assay.services.datasets.replace_dataset_content import replace_dataset_content_by_dataset_id

dataset = MagicMock()
dataset.id = uuid.uuid4()
dataset.name = "My Dataset"

def _make_request(dataset_id: uuid.UUID = None, n_rows: int = 2) -> MagicMock:
    request = MagicMock()
    request.id = dataset_id or uuid.uuid4()
    request.rows = [MagicMock() for _ in range(n_rows)]
    return request


def test_replace_dataset_content_missing_dataset_raises_404():
    request = _make_request()
    session = AsyncMock()

    with patch(
        "assay.services.datasets.replace_dataset_content._get_dataset_or_404",
        new=AsyncMock(side_effect=HTTPException(status_code=404, detail="Dataset not found")),
    ), pytest.raises(HTTPException) as exc:
        asyncio.run(replace_dataset_content_by_dataset_id(request, session))

    assert exc.value.status_code == 404


def test_replace_dataset_content_deletes_existing_rows():
    request = _make_request(dataset_id=dataset.id)
    session = AsyncMock()

    with patch("assay.services.datasets.replace_dataset_content._get_dataset_or_404", new=AsyncMock(
            return_value=dataset)), \
         patch("assay.services.datasets.replace_dataset_content._build_uploaded_dataset_info"):
        asyncio.run(replace_dataset_content_by_dataset_id(request, session))

    session.execute.assert_called_once()
    call_arg = session.execute.call_args[0][0]
    assert isinstance(call_arg, Delete)


def test_replace_dataset_content_adds_new_rows():
    request = _make_request(dataset_id=dataset.id, n_rows=3)
    session = AsyncMock()

    with patch("assay.services.datasets.replace_dataset_content._get_dataset_or_404", new=AsyncMock(
            return_value=dataset)), \
         patch("assay.services.datasets.replace_dataset_content._build_uploaded_dataset_info"):
        asyncio.run(replace_dataset_content_by_dataset_id(request, session))

    session.add_all.assert_called_once()
    added_rows = session.add_all.call_args[0][0]
    assert len(added_rows) == 3
    # the new content is numbered from 1, whatever numbers the old rows had
    assert [row.position for row in added_rows] == [1, 2, 3]
    assert {row.dataset_id for row in added_rows} == {dataset.id}


def test_replace_dataset_content_commits():
    request = _make_request(dataset_id=dataset.id)
    session = AsyncMock()

    with patch("assay.services.datasets.replace_dataset_content._get_dataset_or_404",
               new=AsyncMock(return_value=dataset)), \
         patch("assay.services.datasets.replace_dataset_content._build_uploaded_dataset_info"):
        asyncio.run(replace_dataset_content_by_dataset_id(request, session))

    session.commit.assert_called_once()


def test_replace_dataset_content_returns_build_result():
    request = _make_request(dataset_id=dataset.id)
    session = AsyncMock()
    expected = MagicMock()

    with patch("assay.services.datasets.replace_dataset_content._get_dataset_or_404",
               new=AsyncMock(return_value=dataset)), \
         patch("assay.services.datasets.replace_dataset_content._build_uploaded_dataset_info",
               return_value=expected) as mock_build:
        result = asyncio.run(replace_dataset_content_by_dataset_id(request, session))

    mock_build.assert_called_once()
    assert result == expected
