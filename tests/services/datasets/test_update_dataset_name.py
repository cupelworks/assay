# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.services.datasets.update_dataset_name import (
    _apply_name_update,
    _build_dataset_info,
    _get_dataset_or_404,
)

# --- _get_dataset_or_404 ---

def test_get_dataset_or_404_success():
    dataset_id = uuid.uuid4()
    dataset = MagicMock()
    dataset.id = dataset_id

    session = AsyncMock()
    session.scalar.return_value = dataset

    result = asyncio.run(_get_dataset_or_404(dataset_id, session))

    assert result.id == dataset_id


def test_get_dataset_or_404_failure():
    session = AsyncMock()
    session.scalar.return_value = None
    dataset_id = uuid.uuid4()
    with pytest.raises(HTTPException) as e:
        asyncio.run(_get_dataset_or_404(dataset_id, session))

    assert e.value.status_code == 404
    assert e.value.detail == f"Dataset with id {dataset_id} not found"


# --- _apply_name_update ---

def test_apply_name_update_success():
    dataset = MagicMock()
    dataset.name = "First Name"
    dataset_name = "Second Name"

    session = AsyncMock()

    asyncio.run(_apply_name_update(dataset, dataset_name, session))

    assert dataset.name == dataset_name
    session.commit.assert_called_once()
    
    
# --- _build_dataset_info ---

def test_build_dataset_info():
    dataset = MagicMock()
    dataset.name = "Dataset Name"
    dataset.id = uuid.uuid4()

    dataset_info = _build_dataset_info(dataset)
    assert dataset_info.id == dataset.id
    assert dataset_info.name == dataset.name
