# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.models import TestRunModel, TestSetEntryModel, TestSetModel
from assay.schemas import TestSetEntryID
from assay.services import (
    delete_test_set_by_id,
    delete_test_set_entries_by_id,
    unlink_test_set_entries_by_id,
)

# --- delete_test_set_by_id() ---

def test_test_set_not_found():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(delete_test_set_by_id(test_set_id, session))

    session.delete.assert_not_called()
    session.commit.assert_not_called()
    assert session.scalar.call_count == 1
    assert e.value.status_code == 404
    assert str(test_set_id) in str(e.value.detail)


def test_delete_blocked_when_entry_has_runs():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    with_runs = AsyncMock(return_value={test_set_id})

    with patch("assay.services.test_sets.delete_test_set._find_test_set_or_404"), \
        patch("assay.services.test_sets._common.sets_with_runs", new=with_runs), \
        pytest.raises(HTTPException) as e:
        asyncio.run(delete_test_set_by_id(test_set_id, session))

    session.delete.assert_not_called()
    session.commit.assert_not_called()
    with_runs.assert_awaited_once_with(session, [test_set_id])
    assert e.value.status_code == 409
    assert str(test_set_id) in str(e.value.detail)


def test_test_set_deleted():
    test_set_id = uuid.uuid4()

    test_set_to_delete = TestSetModel(
        id=test_set_id,
        name="Test Set Name",
        created_at=datetime.now().astimezone(),
    )

    session = AsyncMock()
    session.scalar.side_effect = [test_set_to_delete]

    with patch("assay.services.test_sets._common.sets_with_runs",
               new=AsyncMock(return_value=set())):
        asyncio.run(delete_test_set_by_id(test_set_id, session))

    session.delete.assert_called_once_with(test_set_to_delete)
    session.commit.assert_called_once()
    assert session.scalar.call_count == 1

# --- delete_test_set_entries_by_id() ---

def test_delete_entries_test_set_not_found():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(delete_test_set_entries_by_id(test_set_id, [], session))

    session.delete.assert_not_called()
    session.commit.assert_not_called()
    assert session.scalar.call_count == 1
    assert e.value.status_code == 404
    assert str(test_set_id) in str(e.value.detail)
    
    
def test_delete_entries_test_set_entry_not_found_in_specific_test_set():
    test_set_id = uuid.uuid4()
    entry_id = uuid.uuid4()

    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with patch("assay.services.test_sets.delete_test_set._find_test_set_or_404"), \
        pytest.raises(HTTPException) as e:
        asyncio.run(delete_test_set_entries_by_id(test_set_id, [
            TestSetEntryID(id=entry_id),
        ], session))

    session.delete.assert_not_called()
    session.commit.assert_not_called()
    assert session.scalars.call_count == 1
    assert e.value.status_code == 404
    assert f"Test entries with ID '['{entry_id}']'" in str(e.value.detail)
    assert f"not linked to test set with ID '{test_set_id}'" in str(e.value.detail)


def test_delete_entries_that_has_runs():
    test_set_entry_id = uuid.uuid4()

    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[
        test_set_entry_id,
    ]))

    with patch("assay.services.test_sets.delete_test_set._find_test_set_or_404"), \
        patch(
            "assay.services.test_sets.delete_test_set._find_test_set_entries_in_specific_test_set_or_404"
        ), \
        pytest.raises(HTTPException) as e:
        asyncio.run(delete_test_set_entries_by_id(test_set_entry_id, [
            TestSetEntryID(id=test_set_entry_id),
        ], session))

    session.delete.assert_not_called()
    session.commit.assert_not_called()
    assert session.scalars.call_count == 1
    assert e.value.status_code == 409
    assert str(test_set_entry_id) in str(e.value.detail)


def test_delete_entries_happy_path():
    existing_entries = [
        TestSetEntryModel(
            id=uuid.uuid4(),
            test_set_id=uuid.uuid4(),
            test_id=uuid.uuid4(),
            name="Test Set Entry Name",
            input="Test Set Entry Input",
            expected_output="Test Set Entry Expected Output",
            model_output="Test Set Entry Model Output",
            test_type_assignments=[{"name": "ROUGE", "config": None}],
            snapshot_at=datetime.now().astimezone(),
        ),
        TestSetEntryModel(
            id=uuid.uuid4(),
            test_set_id=uuid.uuid4(),
            test_id=uuid.uuid4(),
            name="Test Set Entry Name",
            input="Test Set Entry Input",
            expected_output="Test Set Entry Expected Output",
            model_output="Test Set Entry Model Output",
            test_type_assignments=[{"name": "ROUGE", "config": None}],
            snapshot_at=datetime.now().astimezone(),
        ),
    ]
    
    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=existing_entries))

    with patch("assay.services.test_sets.delete_test_set._find_test_set_or_404"), \
            patch("assay.services.test_sets.delete_test_set._check_given_test_set_entries_have_no_runs_or_409"):
        asyncio.run(delete_test_set_entries_by_id(uuid.uuid4(), [
            TestSetEntryID(id=entry.id)
            for entry in existing_entries
        ], session))

    session.execute.assert_called_once()
    session.commit.assert_called_once()
    assert session.scalars.call_count == 1

    executed_stmt = session.execute.call_args.args[0]
    deleted_ids = set(executed_stmt.whereclause.right.value)
    # set comprehension {...} not list [...]: deleted_ids is a set,
    # and set == list is always False in Python
    assert deleted_ids == {entry.id for entry in existing_entries}

# --- unlink_test_set_entries_by_id() ---

def test_unlink_test_set_not_found():
    test_set_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalar.return_value = None
    
    with pytest.raises(HTTPException) as e:
        asyncio.run(unlink_test_set_entries_by_id(test_set_id, [], session))

    session.execute.assert_not_called()
    session.commit.assert_not_called()
    session.scalar.assert_called_once()
    assert session.scalars.call_count == 0
    assert e.value.status_code == 404
    assert str(test_set_id) in str(e.value.detail)


def test_unlink_test_set_entry_not_found_or_not_linked_to_test_set():
    test_set_id = uuid.uuid4()
    request = [
        TestSetEntryID(id=test_set_id),
        TestSetEntryID(id=uuid.uuid4())
    ]

    session = AsyncMock()
    session.scalar.return_value = TestSetModel(id=test_set_id)
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[
        TestSetEntryModel(id=request[0].id)
    ]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(unlink_test_set_entries_by_id(test_set_id, request, session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    session.execute.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test entries with ID '{[str(request[1].id)]}'" in str(e.value.detail)
    assert f"not linked to test set with ID '{test_set_id}'" in str(e.value.detail)


def test_unlink_test_set_entries_not_found_or_not_linked_to_test_set():
    test_set_id = uuid.uuid4()
    request = [
        TestSetEntryID(id=test_set_id),
        TestSetEntryID(id=uuid.uuid4())
    ]

    session = AsyncMock()
    session.scalar.return_value = TestSetModel(id=test_set_id)
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(unlink_test_set_entries_by_id(test_set_id, request, session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    session.execute.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    detail = str(e.value.detail)
    assert str(request[0].id) in detail
    assert str(request[1].id) in detail
    assert f"not linked to test set with ID '{test_set_id}'" in detail


def test_unlink_test_set_entries_happy_path():
    test_set_id = uuid.uuid4()
    request = [
        TestSetEntryID(id=test_set_id),
        TestSetEntryID(id=uuid.uuid4())
    ]

    session = AsyncMock()
    session.scalar.return_value = TestSetModel(id=test_set_id)
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[
        TestSetEntryModel(id=request[0].id),
        TestSetEntryModel(id=request[1].id),
    ]))

    response = asyncio.run(unlink_test_set_entries_by_id(test_set_id, request, session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    session.execute.assert_called_once()
    session.commit.assert_called_once()
    assert response is None

    executed_stmt = session.execute.call_args.args[0]
    updated_ids = set(executed_stmt.whereclause.right.value)
    # set comprehension {...} not list [...]: updated_ids is a set,
    # and set == list is always False in Python
    assert updated_ids == {request[0].id, request[1].id}

    updated_values = {col.name: bind.value for col, bind in executed_stmt._values.items()}
    assert updated_values == {"test_set_id": None}


def test_unlink_test_set_entry_with_runs_is_allowed():
    test_set_id = uuid.uuid4()
    entry_with_run = TestSetEntryModel(id=uuid.uuid4())
    entry_with_run.runs = [
        TestRunModel(
            id=uuid.uuid4(),
            test_set_entry_id=entry_with_run.id,
            test_set_execution_id=uuid.uuid4(),
        )
    ]
    request = [TestSetEntryID(id=entry_with_run.id)]

    session = AsyncMock()
    session.scalar.return_value = TestSetModel(id=test_set_id)
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[entry_with_run]))

    response = asyncio.run(unlink_test_set_entries_by_id(test_set_id, request, session))

    # Unlike delete, unlink has no runs-based guard, so no extra query is made
    # to check for runs — session.scalars is only called once, by the
    # entries-exist lookup.
    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    session.execute.assert_called_once()
    session.commit.assert_called_once()
    assert response is None
