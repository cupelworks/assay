import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.models import TestPlanEntryModel, TestSetModel
from assay.schemas import TestPlanEntryDetails, TestSetMetadata
from assay.services import get_all_test_plan_entries_metadata


def test_test_plan_not_found():
    test_plan_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_all_test_plan_entries_metadata(test_plan_id, session,))

    session.scalar.assert_called_once()
    session.scalars.assert_not_called()
    assert e.value.status_code == 404
    assert e.value.detail == f"Test plan with ID '{test_plan_id}' not found"
    

def test_no_returned_test_plan_entries_fallback_to_zero():
    test_plan_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalar.side_effect = [test_plan_id, None]
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    session.execute.return_value = MagicMock()
    session.execute.return_value.tuples.return_value.all.return_value = []

    response = asyncio.run(get_all_test_plan_entries_metadata(test_plan_id, session, 1, 5))

    session.scalars.assert_called_once()
    assert session.scalar.call_count == 2
    assert response.total == 0
    assert response.offset == 1
    assert response.limit == 5
    assert response.items == []
    

def test_returned_test_plan_entries():
    test_plan_id = uuid.uuid4()
    first_test_set_id = uuid.uuid4()
    second_test_set_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalar.side_effect = [test_plan_id, 2]
    found_entries = [
        TestPlanEntryModel(
            id=uuid.uuid4(),
            test_plan_id=test_plan_id,
            test_set_id=first_test_set_id,
            test_set=TestSetModel(
                id=first_test_set_id,
                name="First Test Set",
                created_at=datetime.now(),
            ),
        ),
        TestPlanEntryModel(
            id=uuid.uuid4(),
            test_plan_id=test_plan_id,
            test_set_id=second_test_set_id,
            test_set=TestSetModel(
                id=second_test_set_id,
                name="Second Test Set",
                created_at=datetime.now(),
            ),
        ),
    ]
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=found_entries))
    session.execute.return_value = MagicMock()
    session.execute.return_value.tuples.return_value.all.return_value = [
        (first_test_set_id, 5),
        (second_test_set_id, 0),
    ]

    response = asyncio.run(get_all_test_plan_entries_metadata(test_plan_id, session,))

    session.scalars.assert_called_once()
    assert session.scalar.call_count == 2
    assert response.total == 2
    assert response.offset == 0
    assert response.limit == 100
    assert response.items == [
        TestPlanEntryDetails(
            id=found_entries[0].id,
            test_set=TestSetMetadata(
                id=found_entries[0].test_set.id,
                name=found_entries[0].test_set.name,
                created_at=found_entries[0].test_set.created_at,
                entry_count=5,
            ),
        ),
        TestPlanEntryDetails(
            id=found_entries[1].id,
            test_set=TestSetMetadata(
                id=found_entries[1].test_set.id,
                name=found_entries[1].test_set.name,
                created_at=found_entries[1].test_set.created_at,
                entry_count=0,
            ),
        ),
    ]
