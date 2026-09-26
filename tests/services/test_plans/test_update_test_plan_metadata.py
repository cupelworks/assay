import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.models import TestPlanModel
from assay.schemas import ModifyTestPlanRequest
from assay.services import update_test_plan_by_id


def test_update_test_plan_not_found():
    test_plan_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None
    
    with pytest.raises(HTTPException) as e:
        asyncio.run(
            update_test_plan_by_id(
                test_plan_id, MagicMock(), session))

    session.commit.assert_not_called()
    assert session.scalar.call_count == 1
    assert e.value.status_code == 404
    assert str(test_plan_id) in str(e.value.detail)


def test_update_test_plan_name_conflict():
    test_plan_id = uuid.uuid4()
    test_plan_name = "New Test Plan Name"
    request = ModifyTestPlanRequest(name=test_plan_name)

    session = AsyncMock()
    session.scalar.side_effect = [
        TestPlanModel(
            id=test_plan_id,
            name="Previous Test Plan Name",
            created_at=datetime.now(),
        ),
        test_plan_name
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(
            update_test_plan_by_id(
                test_plan_id, request, session
            )
        )

    session.commit.assert_not_called()
    assert session.scalar.call_count == 2
    assert e.value.status_code == 409
    assert str(test_plan_name) in str(e.value.detail)


def test_update_test_plan_name_none_is_noop():
    test_plan_id = uuid.uuid4()
    test_plan_name = "Current Test Plan Name"
    created_at = datetime.now()
    request = ModifyTestPlanRequest(name=None)

    session = AsyncMock()
    session.scalar.side_effect = [
        TestPlanModel(
            id=test_plan_id,
            name=test_plan_name,
            created_at=created_at,
        ),
        2,
    ]

    response = asyncio.run(
        update_test_plan_by_id(
            test_plan_id, request, session
        )
    )

    session.commit.assert_called_once()
    assert session.scalar.call_count == 2
    assert response.id == test_plan_id
    assert response.name == test_plan_name
    assert response.created_at == created_at
    assert response.linked_set_count == 2


def test_update_test_plan_name_unchanged_is_noop():
    test_plan_id = uuid.uuid4()
    test_plan_name = "New and Old Test Plan Name"
    created_at = datetime.now()
    request = ModifyTestPlanRequest(name=test_plan_name)

    session = AsyncMock()
    session.scalar.side_effect = [
        TestPlanModel(
            id=test_plan_id,
            name=test_plan_name,
            created_at=created_at,
        ),
        2,
    ]

    response = asyncio.run(
        update_test_plan_by_id(
            test_plan_id, request, session
        )
    )

    session.commit.assert_called_once()
    assert session.scalar.call_count == 2
    assert response.id == test_plan_id
    assert response.name == test_plan_name
    assert response.created_at == created_at
    assert response.linked_set_count == 2


def test_update_test_plan_name_happy_path():
    test_plan_id = uuid.uuid4()
    test_plan_name = "New Test Plan Name"
    created_at = datetime.now()
    request = ModifyTestPlanRequest(name=test_plan_name)

    session = AsyncMock()
    session.scalar.side_effect = [
        TestPlanModel(
            id=test_plan_id,
            name="Old Test Plan Name",
            created_at=created_at,
        ),
        None,
        2,
    ]

    response = asyncio.run(
        update_test_plan_by_id(
            test_plan_id, request, session
        )
    )

    session.commit.assert_called_once()
    assert session.scalar.call_count == 3
    assert response.id == test_plan_id
    assert response.name == test_plan_name
    assert response.created_at == created_at
    assert response.linked_set_count == 2
