import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from assay.models import TestRunModel, TestStatus
from assay.schemas import (
    ExecutionMetadata,
    ExecutionOrigin,
    RunMetadata,
    RunOrigin,
    StandaloneRunCreationMetadata,
    TestCaseID,
    TestPlanExecutionMetadata,
    TestPlanExecutionRunMetadata,
    TestPlanID,
    TestPlanReplayedExecutionID,
    TestSetEntryID,
    TestSetExecutionID,
    TestSetExecutionMetadata,
    TestSetExecutionRunMetadata,
    TestSetID,
    TestSetReplayedExecutionID,
)
from assay.schemas.runs import TestPlanExecutionID
from assay.services import (
    get_execution_metadata_all_executions,
    get_run_metadata_all_runs,
    get_standalone_run_metadata_all_test_runs,
    get_test_plan_execution_metadata_all_executions,
    get_test_plan_execution_run_metadata_all_runs,
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


# --- get_run_metadata_all_runs() ---

def test_get_run_metadata_all_runs_no_runs_found():
    session = AsyncMock()
    session.scalar.return_value = 0
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))

    response = asyncio.run(get_run_metadata_all_runs(session, 1, 1))

    session.execute.assert_called_once()
    session.scalar.assert_called_once()
    assert response.total == 0
    assert response.offset == 1
    assert response.limit == 1
    assert response.items == []


def test_get_run_metadata_all_runs_happy_path():
    session = AsyncMock()
    session.scalar.return_value = 3

    standalone_test_id = uuid.uuid4()
    test_set_entry_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()
    test_plan_entry_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()

    returned_rows = [
        MagicMock(
            id=uuid.uuid4(),
            status=TestStatus.pending,
            created_at=datetime.now().astimezone(),
            test_id=standalone_test_id,
            test_set_entry_id=None,
            test_set_execution_id=None,
            test_plan_execution_id=None,
        ),
        MagicMock(
            id=uuid.uuid4(),
            status=TestStatus.completed,
            created_at=datetime.now().astimezone(),
            test_id=None,
            test_set_entry_id=test_set_entry_id,
            test_set_execution_id=test_set_execution_id,
            test_plan_execution_id=None,
        ),
        MagicMock(
            id=uuid.uuid4(),
            status=TestStatus.failed,
            created_at=datetime.now().astimezone(),
            test_id=None,
            test_set_entry_id=test_plan_entry_id,
            test_set_execution_id=None,
            test_plan_execution_id=test_plan_execution_id,
        ),
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=returned_rows))

    response = asyncio.run(get_run_metadata_all_runs(session))

    session.execute.assert_called_once()
    session.scalar.assert_called_once()
    assert response.total == 3
    assert response.offset == 0
    assert response.limit == 100

    assert response.items == [
        RunMetadata(
            id=returned_rows[0].id,
            status=returned_rows[0].status,
            created_at=returned_rows[0].created_at,
            origin=RunOrigin.standalone,
            test_case_id=TestCaseID(id=standalone_test_id),
            test_set_entry_id=None,
            test_set_execution_id=None,
            test_plan_execution_id=None,
        ),
        RunMetadata(
            id=returned_rows[1].id,
            status=returned_rows[1].status,
            created_at=returned_rows[1].created_at,
            origin=RunOrigin.test_set,
            test_case_id=None,
            test_set_entry_id=TestSetEntryID(id=test_set_entry_id),
            test_set_execution_id=TestSetExecutionID(id=test_set_execution_id),
            test_plan_execution_id=None,
        ),
        RunMetadata(
            id=returned_rows[2].id,
            status=returned_rows[2].status,
            created_at=returned_rows[2].created_at,
            origin=RunOrigin.test_plan,
            test_case_id=None,
            test_set_entry_id=TestSetEntryID(id=test_plan_entry_id),
            test_set_execution_id=None,
            test_plan_execution_id=TestPlanExecutionID(id=test_plan_execution_id),
        ),
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


# --- get_execution_metadata_all_executions() ---

def test_get_execution_metadata_all_executions_no_executions_found():
    session = AsyncMock()
    session.scalar.side_effect = [0, 0]
    session.execute.side_effect = [
        MagicMock(all=MagicMock(return_value=[])),
        MagicMock(all=MagicMock(return_value=[])),
    ]

    response = asyncio.run(get_execution_metadata_all_executions(session, 1, 1))

    assert session.execute.call_count == 2
    assert session.scalar.call_count == 2
    assert response.total == 0
    assert response.offset == 1
    assert response.limit == 1
    assert response.items == []


def test_get_execution_metadata_all_executions_happy_path_merges_and_sorts_by_created_at():
    session = AsyncMock()
    session.scalar.side_effect = [2, 1]

    test_set_id = uuid.uuid4()
    test_plan_id = uuid.uuid4()
    replayed_test_plan_execution_id = uuid.uuid4()

    newest = datetime.now().astimezone()
    middle = newest - timedelta(minutes=1)
    oldest = newest - timedelta(minutes=2)

    test_set_rows = [
        MagicMock(
            id=uuid.uuid4(),
            created_at=newest,
            test_set_id=test_set_id,
            replayed_execution_id=None,
            run_count=3,
        ),
        MagicMock(
            id=uuid.uuid4(),
            created_at=oldest,
            test_set_id=test_set_id,
            replayed_execution_id=None,
            run_count=0,
        ),
    ]
    test_plan_rows = [
        MagicMock(
            id=uuid.uuid4(),
            created_at=middle,
            test_plan_id=test_plan_id,
            replayed_execution_id=replayed_test_plan_execution_id,
            run_count=5,
        ),
    ]
    session.execute.side_effect = [
        MagicMock(all=MagicMock(return_value=test_set_rows)),
        MagicMock(all=MagicMock(return_value=test_plan_rows)),
    ]

    response = asyncio.run(get_execution_metadata_all_executions(session))

    assert session.execute.call_count == 2
    assert session.scalar.call_count == 2
    assert response.total == 3
    assert response.offset == 0
    assert response.limit == 100

    assert response.items == [
        ExecutionMetadata(
            id=test_set_rows[0].id,
            created_at=newest,
            origin=ExecutionOrigin.test_set,
            run_count=3,
            test_set_id=TestSetID(id=test_set_id),
            test_plan_id=None,
            replayed_test_set_execution_id=None,
            replayed_test_plan_execution_id=None,
        ),
        ExecutionMetadata(
            id=test_plan_rows[0].id,
            created_at=middle,
            origin=ExecutionOrigin.test_plan,
            run_count=5,
            test_set_id=None,
            test_plan_id=TestPlanID(id=test_plan_id),
            replayed_test_set_execution_id=None,
            replayed_test_plan_execution_id=TestPlanReplayedExecutionID(
                id=replayed_test_plan_execution_id
            ),
        ),
        ExecutionMetadata(
            id=test_set_rows[1].id,
            created_at=oldest,
            origin=ExecutionOrigin.test_set,
            run_count=0,
            test_set_id=TestSetID(id=test_set_id),
            test_plan_id=None,
            replayed_test_set_execution_id=None,
            replayed_test_plan_execution_id=None,
        ),
    ]
    # merged order must be created_at descending across both tables, not
    # each table's own rows kept contiguous
    assert [item.origin for item in response.items] == [
        ExecutionOrigin.test_set,
        ExecutionOrigin.test_plan,
        ExecutionOrigin.test_set,
    ]


def test_get_execution_metadata_all_executions_respects_offset_and_limit_after_merge():
    session = AsyncMock()
    session.scalar.side_effect = [2, 2]

    test_set_id = uuid.uuid4()
    test_plan_id = uuid.uuid4()
    now = datetime.now().astimezone()

    test_set_rows = [
        MagicMock(
            id=uuid.uuid4(),
            created_at=now - timedelta(minutes=i),
            test_set_id=test_set_id,
            replayed_execution_id=None,
            run_count=1,
        )
        for i in (0, 2)
    ]
    test_plan_rows = [
        MagicMock(
            id=uuid.uuid4(),
            created_at=now - timedelta(minutes=i),
            test_plan_id=test_plan_id,
            replayed_execution_id=None,
            run_count=1,
        )
        for i in (1, 3)
    ]
    session.execute.side_effect = [
        MagicMock(all=MagicMock(return_value=test_set_rows)),
        MagicMock(all=MagicMock(return_value=test_plan_rows)),
    ]

    response = asyncio.run(get_execution_metadata_all_executions(session, 1, 2))

    assert response.total == 4
    assert response.offset == 1
    assert response.limit == 2
    assert len(response.items) == 2
    # global order is set(0), plan(1), set(2), plan(3) by created_at desc;
    # offset=1, limit=2 must yield plan(1) then set(2)
    assert response.items[0].origin == ExecutionOrigin.test_plan
    assert response.items[0].id == test_plan_rows[0].id
    assert response.items[1].origin == ExecutionOrigin.test_set
    assert response.items[1].id == test_set_rows[1].id


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


# --- get_test_plan_execution_run_metadata_all_runs() ---

def test_get_test_plan_execution_run_metadata_all_runs_test_plan_not_found():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(
            get_test_plan_execution_run_metadata_all_runs(
                test_plan_id, test_plan_execution_id, session
            )
        )

    session.scalar.assert_called_once()
    session.execute.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test plan with ID '{test_plan_id}' not found" in str(e.value.detail)


def test_get_test_plan_execution_run_metadata_all_runs_execution_not_found():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        MagicMock(),
        None,
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(
            get_test_plan_execution_run_metadata_all_runs(
                test_plan_id, test_plan_execution_id, session
            )
        )

    assert session.scalar.call_count == 2
    session.execute.assert_not_called()
    assert e.value.status_code == 404
    assert (
        f"Test plan execution with ID '{test_plan_execution_id}' does not exist"
        in str(e.value.detail)
    )


def test_get_test_plan_execution_run_metadata_all_runs_execution_not_linked_to_test_plan():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        MagicMock(),
        MagicMock(),
        None,
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(
            get_test_plan_execution_run_metadata_all_runs(
                test_plan_id, test_plan_execution_id, session
            )
        )

    assert session.scalar.call_count == 3
    session.execute.assert_not_called()
    assert e.value.status_code == 404
    assert (
        f"Test plan execution with ID '{test_plan_execution_id}' not linked to "
        f"test plan with ID '{test_plan_id}'" in str(e.value.detail)
    )


def test_get_test_plan_execution_run_metadata_all_runs_no_runs_found():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        MagicMock(),
        MagicMock(),
        MagicMock(),
        0,
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))

    response = asyncio.run(
        get_test_plan_execution_run_metadata_all_runs(
            test_plan_id, test_plan_execution_id, session, 1, 1
        )
    )

    session.execute.assert_called_once()
    assert session.scalar.call_count == 4
    assert response.total == 0
    assert response.offset == 1
    assert response.limit == 1
    assert response.items == []


def test_get_test_plan_execution_run_metadata_all_runs_happy_path():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()

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
        get_test_plan_execution_run_metadata_all_runs(
            test_plan_id, test_plan_execution_id, session
        )
    )

    session.execute.assert_called_once()
    assert session.scalar.call_count == 4
    assert response.total == 2
    assert response.offset == 0
    assert response.limit == 100

    assert response.items == [
        TestPlanExecutionRunMetadata(
            id=row.id,
            status=row.status,
            created_at=row.created_at,
            test_set_entry_id=TestSetEntryID(id=row.test_set_entry_id),
            test_plan_execution_id=TestPlanExecutionID(id=test_plan_execution_id),
        )
        for row in returned_rows
    ]
    # test_plan_execution_id must come from the validated parameter, not a
    # (possibly cross-joined/duplicated) per-row column
    assert all(
        item.test_plan_execution_id == TestPlanExecutionID(id=test_plan_execution_id)
        for item in response.items
    )
