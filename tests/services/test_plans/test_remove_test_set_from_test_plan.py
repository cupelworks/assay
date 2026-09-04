import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.schemas import TestSetID
from assay.services import remove_test_sets_from_test_plan_by_id

_PATCH_FIND_TEST_PLAN = ("assay.services.test_plans."
                         "remove_test_set_from_test_plan._find_test_plan_by_id_or_404")
_PATCH_FIND_TEST_SETS = ("assay.services.test_plans."
                         "remove_test_set_from_test_plan._find_test_sets_or_404")
_PATCH_FIND_TEST_PLAN_ENTRIES = ("assay.services.test_plans."
                                 "remove_test_set_from_test_plan._find_test_plan_entries_or_404")


# --- remove_test_sets_from_test_plan_by_id() ---

# Guard 1: test plan existence

def test_raises_404_when_test_plan_not_found():
    # session.scalar returns None → _find_test_plan_by_id_or_404 raises 404 before any write
    session = AsyncMock()
    session.scalar.return_value = None

    test_plan_id = uuid.uuid4()

    with pytest.raises(HTTPException) as e:
        asyncio.run(remove_test_sets_from_test_plan_by_id(test_plan_id, [], session))

    session.execute.assert_not_called()
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
        asyncio.run(remove_test_sets_from_test_plan_by_id(test_plan_id, [
            TestSetID(id=test_set_id),
        ], session))

    session.execute.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert ("Test sets with IDs ['" + str(test_set_id)) in str(e.value.detail)


# Guard 3: link existence (requested test set must be linked to this plan)

def test_raises_404_when_test_set_not_linked_to_test_plan():
    # Both earlier guards pass; scalars returns [] → _find_test_plan_entries_or_404 raises 404
    test_plan_id = uuid.uuid4()
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with patch(_PATCH_FIND_TEST_PLAN), \
            patch(_PATCH_FIND_TEST_SETS, new=AsyncMock(return_value=[test_set_id])), \
            pytest.raises(HTTPException) as e:
        asyncio.run(remove_test_sets_from_test_plan_by_id(test_plan_id, [
            TestSetID(id=test_set_id),
        ], session))

    session.execute.assert_not_called()
    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert f"Test sets with ID '['{test_set_id}']'" in str(e.value.detail)
    assert f"not linked to test plan with ID '{test_plan_id}'" in str(e.value.detail)


# Happy path

def test_happy_path_executes_delete_and_commits():
    # All three guards pass → a single DELETE is executed, then the transaction is committed
    test_plan_id = uuid.uuid4()
    test_set_id_1 = uuid.uuid4()
    test_set_id_2 = uuid.uuid4()

    session = AsyncMock()

    with patch(_PATCH_FIND_TEST_PLAN), \
            patch(_PATCH_FIND_TEST_SETS,
                  new=AsyncMock(return_value=[test_set_id_1, test_set_id_2])), \
            patch(_PATCH_FIND_TEST_PLAN_ENTRIES):
        result = asyncio.run(remove_test_sets_from_test_plan_by_id(test_plan_id, [
            TestSetID(id=test_set_id_1),
            TestSetID(id=test_set_id_2),
        ], session))

    assert result is None
    session.execute.assert_called_once()
    session.commit.assert_called_once()


def test_unlinking_a_run_having_test_set_does_not_raise_and_leaves_test_runs_untouched():
    """Regression test for dev_notes.md note 4: unlinking a test set from a
    plan is unconditional — allowed even if TestRunModel rows already exist
    against that test set via this plan (test_plan_execution_id). There is
    no runs-history guard anywhere in this function's guard chain (only the
    three patched above run at all), so a test set with run history unlinks
    exactly the same way an untouched one does — no raise.

    The already-created runs are guaranteed left untouched, not just
    unasserted: the executed DELETE can only ever affect test_plan_entries
    — TestRunModel has no FK to TestPlanEntryModel at all (a run points at
    a frozen TestSetEntryModel directly, see the cross-cutting freeze note
    in current_implementation.md), so there is no join, cascade, or shared
    table through which this statement could reach test_runs.
    """
    test_plan_id = uuid.uuid4()
    test_set_id = uuid.uuid4()

    session = AsyncMock()

    with patch(_PATCH_FIND_TEST_PLAN), \
            patch(_PATCH_FIND_TEST_SETS, new=AsyncMock(return_value=[test_set_id])), \
            patch(_PATCH_FIND_TEST_PLAN_ENTRIES):
        result = asyncio.run(remove_test_sets_from_test_plan_by_id(test_plan_id, [
            TestSetID(id=test_set_id),
        ], session))

    assert result is None
    session.execute.assert_called_once()
    session.commit.assert_called_once()

    executed_stmt = session.execute.call_args.args[0]
    compiled_sql = str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "test_plan_entries" in compiled_sql
    assert "test_runs" not in compiled_sql


def test_delete_statement_scoped_to_plan_and_requested_test_sets():
    # The executed DELETE must filter on both test_plan_id and the requested test_set_ids,
    # so links belonging to other plans, or to test sets outside this request, are untouched.
    test_plan_id = uuid.uuid4()
    test_set_id_1 = uuid.uuid4()
    test_set_id_2 = uuid.uuid4()

    session = AsyncMock()

    with patch(_PATCH_FIND_TEST_PLAN), \
            patch(_PATCH_FIND_TEST_SETS,
                  new=AsyncMock(return_value=[test_set_id_1, test_set_id_2])), \
            patch(_PATCH_FIND_TEST_PLAN_ENTRIES):
        asyncio.run(remove_test_sets_from_test_plan_by_id(test_plan_id, [
            TestSetID(id=test_set_id_1),
            TestSetID(id=test_set_id_2),
        ], session))

    executed_stmt = session.execute.call_args.args[0]
    # Two chained .where() calls compose into an AND'd BooleanClauseList, not a single
    # binary comparison — inspect .clauses rather than .whereclause.left/.right directly.
    clauses = executed_stmt.whereclause.clauses
    assert len(clauses) == 2

    plan_clause = next(c for c in clauses if c.left.name == "test_plan_id")
    assert plan_clause.right.value == test_plan_id

    set_clause = next(c for c in clauses if c.left.name == "test_set_id")
    # set comprehension {...} not list [...]: order of an IN-clause bind list isn't guaranteed,
    # and set == list is always False in Python
    assert set(set_clause.right.value) == {test_set_id_1, test_set_id_2}


def test_duplicate_test_set_ids_in_request_are_harmless():
    # The same test set ID requested twice must not raise and must still result in
    # exactly one DELETE execution and one commit — no per-ID looping to duplicate.
    test_plan_id = uuid.uuid4()
    test_set_id = uuid.uuid4()

    session = AsyncMock()

    with patch(_PATCH_FIND_TEST_PLAN), \
            patch(_PATCH_FIND_TEST_SETS, new=AsyncMock(return_value=[test_set_id])), \
            patch(_PATCH_FIND_TEST_PLAN_ENTRIES):
        result = asyncio.run(remove_test_sets_from_test_plan_by_id(test_plan_id, [
            TestSetID(id=test_set_id),
            TestSetID(id=test_set_id),
        ], session))

    assert result is None
    session.execute.assert_called_once()
    session.commit.assert_called_once()
