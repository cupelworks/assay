# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
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


def _rows(n: int) -> list[DataSetRowSchema]:
    return [DataSetRowSchema(prompt=f"p{i}", model_output="m", expected_output="e")
            for i in range(n)]


def _add(highest: int | None, rows: list[DataSetRowSchema]) -> AsyncMock:
    """Add rows to a dataset whose highest row number is `highest`; the session."""
    request = MagicMock(rows=rows)
    session = AsyncMock()
    session.add_all = MagicMock()
    # first the dataset's lookup, then its highest row number
    session.scalar.side_effect = [MagicMock(id=uuid.uuid4()), highest]
    with patch("assay.services.datasets.upload_rows_in_dataset._build_uploaded_dataset_info"):
        asyncio.run(upload_new_rows_in_existing_dataset(request, session))
    return session


def test_upload_new_rows_in_existing_dataset_session():
    session = _add(4, _rows(1))

    session.refresh.assert_not_called()  # the dataset's existing rows are never loaded
    session.flush.assert_called_once()
    session.commit.assert_called_once()


def test_added_rows_are_numbered_after_the_highest():
    session = _add(4, _rows(3))

    added = session.add_all.call_args[0][0]
    assert [(row.input, row.position) for row in added] == [("p0", 5), ("p1", 6), ("p2", 7)]


def test_rows_added_to_an_empty_dataset_start_at_1():
    session = _add(None, _rows(2))

    assert [row.position for row in session.add_all.call_args[0][0]] == [1, 2]


# --- _build_uploaded_dataset_info ---

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
