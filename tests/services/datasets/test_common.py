import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.services.datasets._common import _check_name_unique

# --- _check_name_unique ---

dataset_name = "Test Dataset"

def test_dataset_name_unique():
    session = AsyncMock()
    session.scalar.return_value = None

    asyncio.run(_check_name_unique(dataset_name, session))


def test_dataset_name_not_unique():
    session = AsyncMock()
    session.scalar.return_value = MagicMock()

    with pytest.raises(HTTPException) as e:
        asyncio.run(_check_name_unique(dataset_name, session))

    assert e.value.status_code == 409
    assert e.value.detail == f"Dataset name '{dataset_name}' already exists."