import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.models import (
    TestModel,
    TestPlanModel,
    TestSetEntryModel,
    TestSetExecutionModel,
    TestSetModel,
    TestStatus,
    TestTypeAssignmentModel,
)
from assay.schemas import TestPlanID, TestPlanReplayedExecutionID
from assay.services import (
    create_new_live_test_plan_run,
    create_new_live_test_set_run,
    create_new_replay_test_plan_run,
    create_new_replay_test_set_run,
    create_new_standalone_run,
)

# --- create_new_standalone_run() ---

@pytest.fixture(autouse=True)
def added():
    """_add_runs, recorded: each flow hands it the runs it built and their
    executions (tested on its own in test_add_runs.py)."""
    with patch("assay.services.runs.create_new_run._add_runs", new=AsyncMock()) as mock:
        yield mock


def _runs(added) -> list:
    return added.call_args.args[1]


def _execution(added):
    (execution,) = added.call_args.args[2]
    return execution


def test_standalone_test_not_found():
    test_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_standalone_run(test_id, session))

    session.scalars.assert_called_once()
    session.add.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert f"Tests with ids {[str(test_id)]} not found" in str(e.value.detail)
    

def test_standalone_empty_test_type_assignment():
    test_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[
        TestModel(
            id=test_id,
            name="Test Name",
            input="Test Input",
            expected_output="Test Expected Output",
            model_output="Test Model Output",
            test_type_assignments=[]
        ),
    ]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_standalone_run(test_id, session))

    session.scalars.assert_called_once()
    session.add.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert f"No test types assigned to Tests with ids {[str(test_id)]}" in str(e.value.detail)
    
    
def test_standalone_happy_path(added):
    test_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[
        TestModel(
            id=test_id,
            name="Test Name",
            input="Test Input",
            expected_output="Test Expected Output",
            model_output="Test Model Output",
            test_type_assignments=[
                TestTypeAssignmentModel(
                    test_type_name="ROUGE",
                    label="ROUGE",
                    config={"threshold": "0.7"},
                ),
            ],
        )
    ]))

    order = []
    session.commit.side_effect = lambda: order.append("commit")

    with patch(
            "assay.services.runs.create_new_run._dispatch_runs",
            side_effect=lambda run_ids: order.append("dispatch"),
    ) as mock_dispatch:
        response = asyncio.run(create_new_standalone_run(test_id, session))

    (test_run_model,) = _runs(added)

    session.scalars.assert_called_once()
    added.assert_awaited_once()
    session.commit.assert_called_once()
    mock_dispatch.assert_called_once_with([test_run_model.id])
    # Dispatch must happen only after the creating transaction has
    # committed — the worker's own connection isn't
    # guaranteed to see the row until then.
    assert order == ["commit", "dispatch"]
    assert response.id == test_run_model.id
    assert response.created_at == test_run_model.created_at
    assert response.status == test_run_model.status
    assert response.test_case_id.id == test_id

    # The run carries its own frozen copy of the test, sharing its id and
    # taken at the run's creation time - committed in the same transaction.
    frozen_copy = test_run_model.standalone_run
    assert frozen_copy.id == test_run_model.id
    assert frozen_copy.snapshot_at == test_run_model.created_at
    assert (frozen_copy.name, frozen_copy.input) == ("Test Name", "Test Input")
    assert frozen_copy.expected_output == "Test Expected Output"
    assert frozen_copy.model_output == "Test Model Output"
    assert frozen_copy.test_type_assignments == [
        {"name": "ROUGE", "label": "ROUGE", "config": {"threshold": "0.7"},
         "answer_path": None},
    ]


# --- create_new_live_test_set_run() ---

def test_new_live_test_set_run_test_set_not_found():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_live_test_set_run(test_set_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test set with ID '{test_set_id}' not found" in str(e.value.detail)


def test_new_live_test_set_run_test_set_entries_not_found():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = TestSetModel(id=test_set_id)
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_live_test_set_run(test_set_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert f"No Test Set Entries found in Test set with ID '{test_set_id}'" in str(e.value.detail)
    
    
def test_new_live_test_set_run_entries_missing_test_types():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = TestSetModel(id=test_set_id)

    entry_id = uuid.uuid4()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[entry_id]))
    session.execute.return_value = MagicMock(all=MagicMock(
        return_value=[MagicMock(id=entry_id, test_type_assignments=[])]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_live_test_set_run(test_set_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    session.execute.assert_called_once()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert (f"No test types assigned to Test Set Entries with ids {[str(entry_id)]}"
            in str(e.value.detail))


def test_new_live_test_set_run_happy_path(added):
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = TestSetModel(id=test_set_id)

    available_test_set_entry_models = [
        TestSetEntryModel(
            id=uuid.uuid4(),
        )
        for _ in range(0, 3)
    ]
    session.scalars.return_value = MagicMock(all=MagicMock(
        return_value=[entry.id for entry in available_test_set_entry_models]))
    session.execute.return_value = MagicMock(all=MagicMock(
        return_value=[
            MagicMock(id=entry.id, test_type_assignments=["bleu"])
            for entry in available_test_set_entry_models
        ]))

    order = []
    session.commit.side_effect = lambda: order.append("commit")

    with patch(
            "assay.services.runs.create_new_run._dispatch_runs",
            side_effect=lambda run_ids: order.append("dispatch"),
    ) as mock_dispatch:
        response = asyncio.run(create_new_live_test_set_run(test_set_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    session.execute.assert_called_once()
    added.assert_awaited_once()
    session.commit.assert_called_once()
    assert order == ["commit", "dispatch"]

    test_set_execution_model = _execution(added)
    assert test_set_execution_model.test_set_id == test_set_id

    test_runs_models = _runs(added)
    assert len(test_runs_models) == len(available_test_set_entry_models)

    # Every entry must have gotten exactly one run, and every run must point
    # back at the same execution — a plain length check wouldn't catch a
    # run created against the wrong entry or a stray/duplicate execution ID.
    test_runs_models_by_test_set_entry_id = {
        test_run_model.test_set_entry_id: test_run_model
        for test_run_model in test_runs_models
    }
    for available_test_set_entry in available_test_set_entry_models:
        matching_run = test_runs_models_by_test_set_entry_id[available_test_set_entry.id]
        assert matching_run.test_set_execution_id == test_set_execution_model.id

    mock_dispatch.assert_called_once_with(
        [test_run_model.id for test_run_model in test_runs_models]
    )
    assert response.id == test_set_execution_model.id
    assert response.created_at == test_set_execution_model.created_at
    assert response.test_set_id.id == test_set_id
    assert response.run_count == len(available_test_set_entry_models)


# --- create_new_replay_test_set_run() ---

def test_new_replay_test_set_run_test_set_not_found():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_replay_test_set_run(test_set_id, test_set_execution_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test set with ID '{test_set_id}' not found" in str(e.value.detail)


def test_new_replay_test_set_run_execution_not_found():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [TestSetModel(id=test_set_id), None]

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_replay_test_set_run(test_set_id, test_set_execution_id, session))

    assert session.scalar.call_count == 2
    session.scalars.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert (f"Test set execution with ID '{test_set_execution_id}' does not exist"
            in str(e.value.detail))


def test_new_replay_test_set_run_execution_not_linked_to_test_set():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [TestSetModel(id=test_set_id), test_set_execution_id, None]

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_replay_test_set_run(test_set_id, test_set_execution_id, session))

    assert session.scalar.call_count == 3
    session.scalars.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert (f"Test set execution with ID '{test_set_execution_id}' not linked "
            f"to test set with ID '{test_set_id}'") in str(e.value.detail)


def test_new_replay_test_set_run_execution_entries_not_found():
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        TestSetModel(id=test_set_id), test_set_execution_id, test_set_execution_id
    ]
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_replay_test_set_run(test_set_id, test_set_execution_id, session))

    assert session.scalar.call_count == 3
    session.scalars.assert_called_once()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert (f"Test set execution with ID '{test_set_execution_id}' has no "
            f"test set entries") in str(e.value.detail)


def test_new_replay_test_set_run_happy_path(added):
    test_set_id = uuid.uuid4()
    test_set_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        TestSetModel(id=test_set_id), test_set_execution_id, test_set_execution_id
    ]

    available_test_set_entry_models = [
        TestSetEntryModel(
            id=uuid.uuid4(),
        )
        for _ in range(0, 3)
    ]
    session.scalars.return_value = MagicMock(all=MagicMock(
        return_value=[entry.id for entry in available_test_set_entry_models]))

    order = []
    session.commit.side_effect = lambda: order.append("commit")

    with patch(
            "assay.services.runs.create_new_run._dispatch_runs",
            side_effect=lambda run_ids: order.append("dispatch"),
    ) as mock_dispatch:
        response = asyncio.run(
            create_new_replay_test_set_run(test_set_id, test_set_execution_id, session)
        )

    assert session.scalar.call_count == 3
    session.scalars.assert_called_once()
    added.assert_awaited_once()
    session.commit.assert_called_once()
    assert order == ["commit", "dispatch"]

    test_set_execution_model = _execution(added)
    assert isinstance(test_set_execution_model, TestSetExecutionModel)
    assert test_set_execution_model.test_set_id == test_set_id
    assert test_set_execution_model.replayed_execution_id == test_set_execution_id
    # The new execution's own ID must differ from the one being replayed —
    # otherwise the new runs below would silently re-attach to the old
    # execution instead of the new one.
    assert test_set_execution_model.id != test_set_execution_id

    test_runs_models = _runs(added)
    assert len(test_runs_models) == len(available_test_set_entry_models)

    # Every replayed entry must have gotten exactly one new run, and every
    # new run must point back at the NEW execution, not the replayed one —
    # a plain length check wouldn't catch a run left pointing at the old
    # execution ID.
    test_runs_models_by_test_set_entry_id = {
        test_run_model.test_set_entry_id: test_run_model
        for test_run_model in test_runs_models
    }
    for available_test_set_entry in available_test_set_entry_models:
        matching_run = test_runs_models_by_test_set_entry_id[available_test_set_entry.id]
        assert matching_run.test_set_execution_id == test_set_execution_model.id

    mock_dispatch.assert_called_once_with(
        [test_run_model.id for test_run_model in test_runs_models]
    )
    assert response.id == test_set_execution_model.id
    assert response.created_at == test_set_execution_model.created_at
    assert response.test_set_id.id == test_set_id
    assert response.run_count == len(available_test_set_entry_models)
    assert response.replayed_execution_id.id == test_set_execution_id


# --- create_new_live_test_plan_run() ---

def test_new_live_test_plan_run_test_plan_not_found():
    test_plan_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_live_test_plan_run(test_plan_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_not_called()
    session.execute.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test plan with ID '{test_plan_id}' not found" in str(e.value.detail)


def test_new_live_test_plan_run_no_linked_test_sets():
    test_plan_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = TestPlanModel(id=test_plan_id)
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_live_test_plan_run(test_plan_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    session.execute.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert f"Test plan with ID '{test_plan_id}' has no linked test sets" in str(e.value.detail)


def test_new_live_test_plan_run_linked_test_set_has_no_entries():
    test_plan_id = uuid.uuid4()
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = TestPlanModel(id=test_plan_id)
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[test_set_id]))
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_live_test_plan_run(test_plan_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    session.execute.assert_called_once()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert (f"No Test Set Entries found in Test sets with IDs {[str(test_set_id)]}"
            in str(e.value.detail))


def test_new_live_test_plan_run_entries_missing_test_types():
    test_plan_id = uuid.uuid4()
    test_set_id = uuid.uuid4()
    entry_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = TestPlanModel(id=test_plan_id)
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[test_set_id]))
    session.execute.side_effect = [
        MagicMock(all=MagicMock(
            return_value=[MagicMock(id=entry_id, test_set_id=test_set_id)])),
        MagicMock(all=MagicMock(
            return_value=[MagicMock(id=entry_id, test_type_assignments=[])])),
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_live_test_plan_run(test_plan_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    assert session.execute.call_count == 2
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert (f"No test types assigned to Test Set Entries with ids {[str(entry_id)]}"
            in str(e.value.detail))


def test_new_live_test_plan_run_happy_path(added):
    test_plan_id = uuid.uuid4()
    test_set_ids = [uuid.uuid4(), uuid.uuid4()]
    available_entry_ids = [uuid.uuid4() for _ in range(3)]

    session = AsyncMock()
    session.scalar.return_value = TestPlanModel(id=test_plan_id)
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=test_set_ids))
    session.execute.side_effect = [
        MagicMock(all=MagicMock(return_value=[
            MagicMock(id=entry_id, test_set_id=test_set_ids[i % len(test_set_ids)])
            for i, entry_id in enumerate(available_entry_ids)
        ])),
        MagicMock(all=MagicMock(return_value=[
            MagicMock(id=entry_id, test_type_assignments=["bleu"])
            for entry_id in available_entry_ids
        ])),
    ]

    order = []
    session.commit.side_effect = lambda: order.append("commit")

    with patch(
            "assay.services.runs.create_new_run._dispatch_runs",
            side_effect=lambda run_ids: order.append("dispatch"),
    ) as mock_dispatch:
        response = asyncio.run(create_new_live_test_plan_run(test_plan_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_called_once()
    assert session.execute.call_count == 2
    added.assert_awaited_once()
    session.commit.assert_called_once()
    assert order == ["commit", "dispatch"]

    test_plan_execution_model = _execution(added)
    assert test_plan_execution_model.test_plan_id == test_plan_id

    test_runs_models = _runs(added)
    assert len(test_runs_models) == len(available_entry_ids)

    # Every entry must have gotten exactly one run, and every run must point
    # back at the same execution — a plain length check wouldn't catch a
    # run created against the wrong entry or a stray/duplicate execution ID.
    test_runs_models_by_test_set_entry_id = {
        test_run_model.test_set_entry_id: test_run_model
        for test_run_model in test_runs_models
    }
    for entry_id in available_entry_ids:
        matching_run = test_runs_models_by_test_set_entry_id[entry_id]
        assert matching_run.test_plan_execution_id == test_plan_execution_model.id

    mock_dispatch.assert_called_once_with(
        [test_run_model.id for test_run_model in test_runs_models]
    )
    assert response.id == test_plan_execution_model.id
    assert response.created_at == test_plan_execution_model.created_at
    assert response.test_plan_id.id == test_plan_id
    assert response.run_count == len(available_entry_ids)


# --- create_new_replay_test_plan_run() ---

def test_new_replay_test_plan_test_plan_not_found():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalar.return_value = None
    
    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_replay_test_plan_run(
            test_plan_id, test_plan_execution_id, session))

    session.scalar.assert_called_once()
    session.scalars.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test plan with ID '{test_plan_id}' not found" in str(e.value.detail)
    assert str(test_plan_execution_id) not in str(e.value.detail)


def test_new_replay_test_plan_test_plan_execution_id_not_found():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        TestPlanModel(id=test_plan_id),
        None
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_replay_test_plan_run(
            test_plan_id, test_plan_execution_id, session
        ))

    session.scalars.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    assert session.scalar.call_count == 2
    assert e.value.status_code == 404
    assert (f"Test plan execution with ID '{test_plan_execution_id}' does not exist"
            in str(e.value.detail))
    assert str(test_plan_id) not in str(e.value.detail)


def test_new_replay_test_plan_test_plan_execution_id_not_linked_to_specific_test_plan_id():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        TestPlanModel(id=test_plan_id),
        test_plan_execution_id,
        None,
    ]

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_replay_test_plan_run(
            test_plan_id, test_plan_execution_id, session
        ))

    session.scalars.assert_not_called()
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    assert session.scalar.call_count == 3
    assert e.value.status_code == 404
    assert (f"Test plan execution with ID '{test_plan_execution_id}' not linked "
            f"to test plan with ID '{test_plan_id}'") in str(e.value.detail)


def test_new_replay_test_plan_execution_id_entries_not_found():
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        TestPlanModel(id=test_plan_id),
        test_plan_execution_id,
        test_plan_execution_id,
    ]

    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with pytest.raises(HTTPException) as e:
        asyncio.run(create_new_replay_test_plan_run(
            test_plan_id, test_plan_execution_id, session
        ))

    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.scalars.assert_called_once()
    assert session.scalar.call_count == 3
    assert e.value.status_code == 409
    assert (f"Test plan execution with ID '{test_plan_execution_id}' has no test set entries"
            in str(e.value.detail))
    assert str(test_plan_id) not in str(e.value.detail)


def test_new_replay_test_plan_happy_path(added):
    test_plan_id = uuid.uuid4()
    test_plan_execution_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [
        TestPlanModel(id=test_plan_id),
        test_plan_execution_id,
        test_plan_execution_id,
    ]

    test_plan_execution_entry_id = uuid.uuid4()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[
        test_plan_execution_entry_id,
    ]))

    order = []
    session.commit.side_effect = lambda: order.append("commit")

    with patch(
            "assay.services.runs.create_new_run._dispatch_runs",
            side_effect=lambda run_ids: order.append("dispatch"),
    ) as mock_dispatch:
        response = asyncio.run(create_new_replay_test_plan_run(
            test_plan_id, test_plan_execution_id, session
        ))

    session.scalars.assert_called_once()
    added.assert_awaited_once()
    assert session.scalar.call_count == 3
    assert order == ["commit", "dispatch"]

    test_plan_execution_model = _execution(added)
    assert test_plan_execution_model.test_plan_id == test_plan_id
    assert test_plan_execution_model.replayed_execution_id == test_plan_execution_id

    test_run_models = _runs(added)
    assert len(test_run_models) == 1

    test_run_model = next(iter(test_run_models))
    assert test_run_model.test_set_entry_id == test_plan_execution_entry_id
    assert test_run_model.test_plan_execution_id == test_plan_execution_model.id
    assert test_run_model.status == TestStatus.pending
    mock_dispatch.assert_called_once_with([test_run_model.id])
    assert response.id == test_plan_execution_model.id
    assert response.created_at == test_plan_execution_model.created_at
    assert response.test_plan_id == TestPlanID(id=test_plan_id)
    assert response.run_count == len(test_run_models)
    assert response.replayed_execution_id == TestPlanReplayedExecutionID(id=test_plan_execution_id)
