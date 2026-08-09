import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.models import TestRunModel, TestStatus
from assay.schemas import (
    StandaloneRunCreationMetadata,
    TestCaseID,
    TestPlanExecutionMetadata,
    TestPlanID,
    TestPlanReplayedExecutionID,
    TestSetEntryID,
    TestSetExecutionID,
    TestSetExecutionMetadata,
    TestSetExecutionRunMetadata,
    TestSetID,
    TestSetReplayedExecutionID,
)
from assay.services import (
    get_standalone_run_metadata_all_test_runs,
    get_test_plan_execution_metadata_all_executions,
    get_test_set_execution_metadata_all_executions,
    get_test_set_execution_run_metadata_all_runs,
)

# --- _get_standalone_run_metadata_all_test_runs() ---

def test_get_standalone_run_metadata_all_test_runs_test_not_found():
    test_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_standalone_run_metadata_all_test_runs(test_id, session))

    session.scalar.assert_called_once()
    session.execute.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test with id {str(test_id)} not found" in str(e.value.detail)


def test_get_standalone_run_metadata_all_test_runs_no_runs_found():
    test_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        test_id,
        None,
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))

    response = asyncio.run(get_standalone_run_metadata_all_test_runs(test_id, session, 1, 1))

    session.execute.assert_called_once()
    assert session.scalar.call_count == 2
    assert response.total == 0
    assert response.offset == 1
    assert response.limit == 1
    assert response.items == []


def test_get_standalone_run_metadata_all_test_runs_happy_path():
    test_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalar.side_effect = [
        test_id,
        2,
    ]
    returned_test_run_models = [
        TestRunModel(
            id=uuid.uuid4(),
            status=TestStatus.pending,
            created_at=datetime.now().astimezone(),
        ),
        TestRunModel(
            id=uuid.uuid4(),
            status=TestStatus.running,
            created_at=datetime.now().astimezone(),
        )
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=returned_test_run_models))

    response = asyncio.run(get_standalone_run_metadata_all_test_runs(test_id, session))

    session.execute.assert_called_once()
    assert session.scalar.call_count == 2
    assert response.total == 2
    assert response.offset == 0
    assert response.limit == 100

    assert response.items == [
        StandaloneRunCreationMetadata(
            id=test_run_model.id,
            status=test_run_model.status,
            created_at=test_run_model.created_at,
            test_case_id=TestCaseID(id=test_id),
        )
        for test_run_model in returned_test_run_models
    ]


# --- get_test_set_execution_metadata_all_executions() ---

def test_get_test_set_execution_metadata_all_executions_test_set_not_found():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_test_set_execution_metadata_all_executions(test_set_id, session))

    session.scalar.assert_called_once()
    session.execute.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test set with ID '{test_set_id}' not found" in str(e.value.detail)


def test_get_test_set_execution_metadata_all_executions_no_executions_found():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        MagicMock(),
        0,
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))

    response = asyncio.run(
        get_test_set_execution_metadata_all_executions(test_set_id, session, 1, 1)
    )

    session.execute.assert_called_once()
    assert session.scalar.call_count == 2
    assert response.total == 0
    assert response.offset == 1
    assert response.limit == 1
    assert response.items == []


def test_get_test_set_execution_metadata_all_executions_happy_path():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        MagicMock(),
        2,
    ]
    replayed_execution_id = uuid.uuid4()
    returned_rows = [
        MagicMock(
            id=uuid.uuid4(),
            created_at=datetime.now().astimezone(),
            replayed_execution_id=None,
            run_count=3,
        ),
        MagicMock(
            id=uuid.uuid4(),
            created_at=datetime.now().astimezone(),
            replayed_execution_id=replayed_execution_id,
            run_count=0,
        ),
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=returned_rows))

    response = asyncio.run(get_test_set_execution_metadata_all_executions(test_set_id, session))

    session.execute.assert_called_once()
    assert session.scalar.call_count == 2
    assert response.total == 2
    assert response.offset == 0
    assert response.limit == 100

    assert response.items == [
        TestSetExecutionMetadata(
            id=row.id,
            created_at=row.created_at,
            test_set_id=TestSetID(id=test_set_id),
            run_count=row.run_count,
            replayed_execution_id=TestSetReplayedExecutionID(id=row.replayed_execution_id)
            if row.replayed_execution_id else None,
        )
        for row in returned_rows
    ]
    # run_count must track each row's own aggregate, not the page size
    assert response.items[0].run_count == 3
    assert response.items[1].run_count == 0


# --- get_test_plan_execution_metadata_all_executions() ---

def test_get_test_plan_execution_metadata_all_executions_test_plan_not_found():
    test_plan_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_test_plan_execution_metadata_all_executions(test_plan_id, session))

    session.scalar.assert_called_once()
    session.execute.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test plan with ID '{test_plan_id}' not found" in str(e.value.detail)


def test_get_test_plan_execution_metadata_all_executions_no_executions_found():
    test_plan_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        MagicMock(),
        0,
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))

    response = asyncio.run(
        get_test_plan_execution_metadata_all_executions(test_plan_id, session, 1, 1)
    )

    session.execute.assert_called_once()
    assert session.scalar.call_count == 2
    assert response.total == 0
    assert response.offset == 1
    assert response.limit == 1
    assert response.items == []


def test_get_test_plan_execution_metadata_all_executions_happy_path():
    test_plan_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        MagicMock(),
        2,
    ]
    replayed_execution_id = uuid.uuid4()
    returned_rows = [
        MagicMock(
            id=uuid.uuid4(),
            created_at=datetime.now().astimezone(),
            replayed_execution_id=None,
            run_count=5,
        ),
        MagicMock(
            id=uuid.uuid4(),
            created_at=datetime.now().astimezone(),
            replayed_execution_id=replayed_execution_id,
            run_count=0,
        ),
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=returned_rows))

    response = asyncio.run(get_test_plan_execution_metadata_all_executions(test_plan_id, session))

    session.execute.assert_called_once()
    assert session.scalar.call_count == 2
    assert response.total == 2
    assert response.offset == 0
    assert response.limit == 100

    assert response.items == [
        TestPlanExecutionMetadata(
            id=row.id,
            created_at=row.created_at,
            test_plan_id=TestPlanID(id=test_plan_id),
            run_count=row.run_count,
            replayed_execution_id=TestPlanReplayedExecutionID(id=row.replayed_execution_id)
            if row.replayed_execution_id else None,
        )
        for row in returned_rows
    ]
    # run_count must track each row's own aggregate, not the page size
    assert response.items[0].run_count == 5
    assert response.items[1].run_count == 0


# --- get_test_set_execution_run_metadata_all_runs() ---

def test_get_test_set_execution_run_metadata_all_runs_test_set_not_found():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(
            get_test_set_execution_run_metadata_all_runs(
                test_set_id, test_set_execution_id, session
            )
        )

    session.scalar.assert_called_once()
    session.execute.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test set with ID '{test_set_id}' not found" in str(e.value.detail)


def test_get_test_set_execution_run_metadata_all_runs_execution_not_found():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        MagicMock(),
        None,
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(
            get_test_set_execution_run_metadata_all_runs(
                test_set_id, test_set_execution_id, session
            )
        )

    assert session.scalar.call_count == 2
    session.execute.assert_not_called()
    assert e.value.status_code == 404
    assert (
        f"Test set execution with ID '{test_set_execution_id}' does not exist"
        in str(e.value.detail)
    )


def test_get_test_set_execution_run_metadata_all_runs_execution_not_linked_to_test_set():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        MagicMock(),
        MagicMock(),
        None,
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(
            get_test_set_execution_run_metadata_all_runs(
                test_set_id, test_set_execution_id, session
            )
        )

    assert session.scalar.call_count == 3
    session.execute.assert_not_called()
    assert e.value.status_code == 404
    assert (
        f"Test set execution with ID '{test_set_execution_id}' not linked to "
        f"test set with ID '{test_set_id}'" in str(e.value.detail)
    )


def test_get_test_set_execution_run_metadata_all_runs_no_runs_found():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        MagicMock(),
        MagicMock(),
        MagicMock(),
        0,
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))

    response = asyncio.run(
        get_test_set_execution_run_metadata_all_runs(
            test_set_id, test_set_execution_id, session, 1, 1
        )
    )

    session.execute.assert_called_once()
    assert session.scalar.call_count == 4
    assert response.total == 0
    assert response.offset == 1
    assert response.limit == 1
    assert response.items == []


def test_get_test_set_execution_run_metadata_all_runs_happy_path():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        MagicMock(),
        MagicMock(),
        MagicMock(),
        2,
    ]
    returned_rows = [
        MagicMock(
            id=uuid.uuid4(),
            status=TestStatus.completed,
            created_at=datetime.now().astimezone(),
            test_set_entry_id=uuid.uuid4(),
        ),
        MagicMock(
            id=uuid.uuid4(),
            status=TestStatus.pending,
            created_at=datetime.now().astimezone(),
            test_set_entry_id=uuid.uuid4(),
        ),
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=returned_rows))

    response = asyncio.run(
        get_test_set_execution_run_metadata_all_runs(test_set_id, test_set_execution_id, session)
    )

    session.execute.assert_called_once()
    assert session.scalar.call_count == 4
    assert response.total == 2
    assert response.offset == 0
    assert response.limit == 100

    assert response.items == [
        TestSetExecutionRunMetadata(
            id=row.id,
            status=row.status,
            created_at=row.created_at,
            test_set_entry_id=TestSetEntryID(id=row.test_set_entry_id),
            test_set_execution_id=TestSetExecutionID(id=test_set_execution_id),
        )
        for row in returned_rows
    ]
    # test_set_execution_id must come from the validated parameter, not a
    # (possibly cross-joined/duplicated) per-row column
    assert all(
        item.test_set_execution_id == TestSetExecutionID(id=test_set_execution_id)
        for item in response.items
    )
