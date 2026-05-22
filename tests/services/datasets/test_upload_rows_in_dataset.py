import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.schemas import DataSetRowSchema
from assay.services.datasets import upload_new_rows_in_existing_dataset
from assay.services.datasets.upload_rows_in_dataset import _build_uploaded_dataset_info

# --- upload_new_rows_in_existing_dataset ---

def test_upload_new_rows_in_existing_dataset_404():
    request = MagicMock()
    request.id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(upload_new_rows_in_existing_dataset(request, session))

    assert e.value.status_code == 404
    assert e.value.detail == f"Dataset with id {request.id} not found"


def test_upload_new_rows_in_existing_dataset_session():
    request = MagicMock()

    session = AsyncMock()

    with patch("assay.services.datasets.upload_rows_in_dataset._build_uploaded_dataset_info"):
        asyncio.run(upload_new_rows_in_existing_dataset(request, session))

    session.refresh.assert_called_once()
    session.flush.assert_called_once()
    session.commit.assert_called_once()


def test_upload_new_rows_in_existing_dataset_rows_added():
    request = MagicMock()
    request.rows = [
        DataSetRowSchema(
            prompt="p",
            model_output="m",
            expected_output="e")
        for _ in range(3)
    ]

    dataset = MagicMock()
    dataset.rows = []

    session = AsyncMock()
    session.scalar.return_value = dataset

    with patch("assay.services.datasets.upload_rows_in_dataset._build_uploaded_dataset_info"):
        asyncio.run(upload_new_rows_in_existing_dataset(request, session))

    assert len(dataset.rows) == 3


def test_build_uploaded_dataset_info():
    dataset = MagicMock()
    dataset.id = uuid.uuid4()
    dataset.name = "My Dataset"

    row1 = MagicMock()
    row1.id = uuid.uuid4()
    row2 = MagicMock()
    row2.id = uuid.uuid4()

    result = _build_uploaded_dataset_info(dataset, [row1, row2])

    assert result.dataset.id == dataset.id
    assert result.dataset.name == dataset.name
    assert result.loaded.n == 2
    assert result.loaded.ids == [row1.id, row2.id]
