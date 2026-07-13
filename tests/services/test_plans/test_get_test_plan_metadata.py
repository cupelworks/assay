import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.models import TestPlanModel
from assay.schemas import TestPlanMetadata
from assay.services import get_all_test_plans_metadata, get_test_plan_metadata_by_id

# --- get_all_test_plans_metadata() ---

def test_test_plans_metadata_total_none_defaults_to_zero():
    session = AsyncMock()
    session.scalar.return_value = None
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    response = asyncio.run(get_all_test_plans_metadata(session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    assert response.total == 0
    assert response.offset == 0
    assert response.limit == 100
    assert response.items == []


def test_test_plans_metadata_happy_path():
    session = AsyncMock()
    session.scalar.return_value = 2
    existing_test_plans = [
        TestPlanModel(
            id=uuid.uuid4(),
            name=f"Test plan {uuid.uuid4()}",
            created_at=datetime.now(),
        ),
        TestPlanModel(
            id=uuid.uuid4(),
            name=f"Test plan {uuid.uuid4()}",
            created_at=datetime.now(),
        )
    ]
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=existing_test_plans))

    response = asyncio.run(get_all_test_plans_metadata(session, 1, 2))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    assert response.total == 2
    assert response.offset == 1
    assert response.limit == 2
    assert response.items == [TestPlanMetadata(
        id=test_plan.id,
        name=test_plan.name,
        created_at=test_plan.created_at,
    )
        for test_plan in existing_test_plans
    ]

# --- get_test_plan_metadata_by_id() ---

def test_test_plan_metadata_not_found():
    test_plan_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_test_plan_metadata_by_id(test_plan_id, session))

    session.scalar.assert_called_once()
    assert e.value.status_code == 404
    assert f"Test plan with ID '{test_plan_id}'" in str(e.value.detail)


def test_test_plan_metadata_happy_path():
    test_plan_id = uuid.uuid4()
    test_plan_name = "Test plan name"
    created_at = datetime.now()

    session = AsyncMock()
    session.scalar.return_value = TestPlanModel(
        id=test_plan_id,
        name=test_plan_name,
        created_at=created_at,
    )

    response = asyncio.run(get_test_plan_metadata_by_id(test_plan_id, session))

    session.scalar.assert_called_once()
    assert response.id == test_plan_id
    assert response.name == test_plan_name
    assert response.created_at == created_at
