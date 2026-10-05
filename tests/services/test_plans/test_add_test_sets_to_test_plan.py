# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.models import TestPlanEntryModel
from assay.schemas import TestSetID
from assay.services import add_test_sets_to_test_plan_by_id

_PATCH_FIND_TEST_PLAN = ("assay.services.test_plans."
                         "add_test_sets_to_test_plan._find_test_plan_by_id_or_404")
_PATCH_FIND_TEST_SETS = ("assay.services.test_plans."
                         "add_test_sets_to_test_plan._find_test_sets_or_404")
_PATCH_CHECK_409 = ("assay.services.test_plans."
                    "add_test_sets_to_test_plan._check_test_set_not_in_test_plan_or_409")


# --- add_test_sets_to_test_plan_by_id() ---

# Guard 1: test plan existence

def test_raises_404_when_test_plan_not_found():
    # session.scalar returns None → _find_test_plan_by_id_or_404 raises 404 before any write
    session = AsyncMock()
    session.scalar.return_value = None

    test_plan_id = uuid.uuid4()

    with pytest.raises(HTTPException) as e:
        asyncio.run(add_test_sets_to_test_plan_by_id(test_plan_id, [], session))

    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert ("Test plan with ID '" + str(test_plan_id)) in str(e.value.detail)


# Guard 2: test set IDs existence

def test_raises_404_when_test_set_not_found():
    # Test plan check passes; scalars returns [] → _find_test_sets_or_404 raises 404
    test_plan_id = uuid.uuid4()
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with patch(_PATCH_FIND_TEST_PLAN), \
            pytest.raises(HTTPException) as e:
        asyncio.run(add_test_sets_to_test_plan_by_id(test_plan_id, [
            TestSetID(id=test_set_id),], session))

    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert ("Test sets with IDs ['" + str(test_set_id)) in str(e.value.detail)


# Guard 3: duplicate-link detection

def test_raises_409_when_test_set_already_in_test_plan():
    # Both earlier guards pass (guard 2 patched to return this test_set_id);
    # scalars returns the test_set_id → 409 for the existing link
    test_plan_id = uuid.uuid4()
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    # The 409 check queries TestPlanEntryModel.test_set_id, so the mock returns the UUID directly
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[test_set_id]))

    with patch(_PATCH_FIND_TEST_PLAN), \
        patch(_PATCH_FIND_TEST_SETS, new=AsyncMock(return_value=[test_set_id])), \
        pytest.raises(HTTPException) as e:
        asyncio.run(add_test_sets_to_test_plan_by_id(test_plan_id, [
            TestSetID(id=test_set_id),], session))

    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert ("Test sets with ID '['" + str(test_set_id)) in str(e.value.detail)
    assert str(test_plan_id) in str(e.value.detail)


# Happy path

def test_returns_correct_entry_ids():
    # Two valid test sets → response contains exactly two TestPlanEntryID objects with UUIDs.
    # _find_test_sets_or_404 is not patched so session.scalars drives it directly.
    test_plan_id = uuid.uuid4()
    test_set_id_1 = uuid.uuid4()
    test_set_id_2 = uuid.uuid4()
    session = AsyncMock()
    session.scalars.return_value = MagicMock(
        all=MagicMock(return_value=[test_set_id_1, test_set_id_2])
    )

    with patch(_PATCH_FIND_TEST_PLAN), \
         patch(_PATCH_CHECK_409):
        result = asyncio.run(add_test_sets_to_test_plan_by_id(
            test_plan_id, [TestSetID(id=test_set_id_1), TestSetID(id=test_set_id_2)], session
        ))

    assert len(result) == 2
    assert all(isinstance(entry.id, uuid.UUID) for entry in result)


def test_duplicate_test_set_ids_produce_one_entry():
    # Even if the request lists the same test set twice, _find_test_sets_or_404's IN-clause
    # query returns it once (simulated here via session.scalars), so only one entry is made.
    test_plan_id = uuid.uuid4()
    test_set_id = uuid.uuid4()
    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[test_set_id]))

    with patch(_PATCH_FIND_TEST_PLAN), \
         patch(_PATCH_CHECK_409):
        result = asyncio.run(add_test_sets_to_test_plan_by_id(
            test_plan_id, [TestSetID(id=test_set_id), TestSetID(id=test_set_id)], session
        ))

    assert len(result) == 1


def test_entry_fields_set_correctly():
    # test_plan_id and test_set_id on the persisted entry must match the inputs
    test_plan_id = uuid.uuid4()
    test_set_id = uuid.uuid4()
    session = AsyncMock()

    with patch(_PATCH_FIND_TEST_PLAN), \
         patch(_PATCH_FIND_TEST_SETS, new=AsyncMock(return_value=[test_set_id])), \
         patch(_PATCH_CHECK_409):
        asyncio.run(add_test_sets_to_test_plan_by_id(
            test_plan_id, [TestSetID(id=test_set_id)], session
        ))

    entry = session.add_all.call_args[0][0][0]
    assert entry.test_plan_id == test_plan_id
    assert entry.test_set_id == test_set_id
    assert isinstance(entry.id, uuid.UUID)


def test_session_add_all_called_with_orm_models():
    # session.add_all must receive TestPlanEntryModel instances, not Pydantic schema objects
    test_plan_id = uuid.uuid4()
    test_set_id = uuid.uuid4()
    session = AsyncMock()

    with patch(_PATCH_FIND_TEST_PLAN), \
         patch(_PATCH_FIND_TEST_SETS, new=AsyncMock(return_value=[test_set_id])), \
         patch(_PATCH_CHECK_409):
        asyncio.run(add_test_sets_to_test_plan_by_id(
            test_plan_id, [TestSetID(id=test_set_id)], session
        ))

    session.add_all.assert_called_once()
    added = session.add_all.call_args[0][0]
    assert all(isinstance(entry, TestPlanEntryModel) for entry in added)


def test_session_commit_called():
    # commit must be called after add_all to persist the entries
    test_plan_id = uuid.uuid4()
    test_set_id = uuid.uuid4()
    session = AsyncMock()

    with patch(_PATCH_FIND_TEST_PLAN), \
         patch(_PATCH_FIND_TEST_SETS, new=AsyncMock(return_value=[test_set_id])), \
         patch(_PATCH_CHECK_409):
        asyncio.run(add_test_sets_to_test_plan_by_id(
            test_plan_id, [TestSetID(id=test_set_id)], session
        ))

    session.commit.assert_called_once()
