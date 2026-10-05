# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from assay.models import TestPlanModel
from assay.services import delete_test_plan_by_id


def test_test_plan_not_found():
    test_plan_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(delete_test_plan_by_id(test_plan_id, session))

    session.scalar.assert_called_once()
    session.delete.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test plan with ID '{test_plan_id}' not found" in str(e.value.detail)


def test_test_plan_has_runs():
    test_plan_id = uuid.uuid4()

    session = AsyncMock()
    with_runs = AsyncMock(return_value={test_plan_id})

    with patch("assay.services.test_plans.delete_test_plan._find_test_plan_by_id_or_404"), \
        patch("assay.services.test_plans._common.plans_with_runs", new=with_runs), \
        pytest.raises(HTTPException) as e:
        asyncio.run(delete_test_plan_by_id(test_plan_id, session))

    with_runs.assert_awaited_once_with(session, [test_plan_id])
    session.delete.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert (f"Test plan with ID '{test_plan_id}' has at least one run"
            f", therefore it cannot be deleted") in str(e.value.detail)


def test_delete_test_plan_happy_path():
    test_plan_id = uuid.uuid4()

    test_plan_to_delete = TestPlanModel(
        id=test_plan_id,
        name="Test Plan Name",
        created_at=datetime.now().astimezone(),
    )

    session = AsyncMock()
    session.scalar.side_effect = [test_plan_to_delete]

    with patch("assay.services.test_plans._common.plans_with_runs",
               new=AsyncMock(return_value=set())):
        asyncio.run(delete_test_plan_by_id(test_plan_id, session))

    assert session.scalar.call_count == 1
    session.delete.assert_called_once_with(test_plan_to_delete)
    session.commit.assert_called_once()
