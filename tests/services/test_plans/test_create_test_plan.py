# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from assay.schemas import TestPlanName
from assay.services import create_new_test_plan


def test_test_plan_name_not_unique():
    request = TestPlanName(
        name="Test Plan Name",
    )

    session = AsyncMock()
    session.scalar.return_value = "Test Plan Name"

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_test_plan(request, session))

    session.add.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert request.name in str(e.value.detail)


def test_create_test_plan_happy_path():
    request = TestPlanName(
        name="Test Plan Name",
    )

    session = AsyncMock()
    session.scalar.return_value = None

    response = asyncio.run(create_new_test_plan(request, session))

    session.add.assert_called_once()
    session.commit.assert_called_once()
    assert response.id == session.add.call_args.args[0].id
    assert response.name == request.name
