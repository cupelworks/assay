import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.models import TestSetEntryModel
from assay.schemas import TestCaseID
from assay.services import add_tests_to_test_set_by_test_id

_PATCH_FIND_TEST_SET = "assay.services.test_sets.add_tests_to_test_set._find_test_set_or_404"
_PATCH_FIND_TESTS = ("assay.services.test_sets."
                     "add_tests_to_test_set._find_all_tests_with_details_or_404")
_PATCH_CHECK_409 = ("assay.services.test_sets."
                    "add_tests_to_test_set._check_tests_not_in_test_set_or_409")


def _make_test(
        name: str = "Test",
        input_val: str = "the prompt",
        expected_output: str | None = "expected",
        model_output: str | None = "actual",
        type_names: list[str] | None = None,
        type_configs: dict[str, dict | None] | None = None,
) -> MagicMock:
    test = MagicMock()
    test.id = uuid.uuid4()
    test.name = name
    test.input = input_val
    test.expected_output = expected_output
    test.model_output = model_output
    assignments = []
    for type_name in (type_names or []):
        assignment = MagicMock()
        assignment.test_type_name = type_name
        assignment.label = type_name
        assignment.config = (type_configs or {}).get(type_name)
        assignment.answer_path = None
        assignments.append(assignment)
    test.test_type_assignments = assignments
    return test


# --- add_tests_to_test_set_by_test_id() ---

# Guard 1: test set existence

def test_raises_404_when_test_set_not_found():
    # session.scalar returns None → _find_test_set_or_404 raises 404 before any write
    session = AsyncMock()
    session.scalar.return_value = None

    test_set_id = uuid.uuid4()

    with pytest.raises(HTTPException) as e:
        asyncio.run(add_tests_to_test_set_by_test_id(test_set_id, [], session))

    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert ("Test set with ID '" + str(test_set_id)) in str(e.value.detail)


# Guard 2: test IDs existence

def test_raises_404_when_test_not_found():
    # Test set check passes; scalars returns [] → _find_all_tests_with_details_or_404 raises 404
    test_set_id = uuid.uuid4()
    test_case_id = uuid.uuid4()

    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with patch(_PATCH_FIND_TEST_SET), \
            pytest.raises(HTTPException) as e:
        asyncio.run(add_tests_to_test_set_by_test_id(test_set_id, [
            TestCaseID(id=test_case_id),], session))

    session.commit.assert_not_called()
    assert e.value.status_code == 404
    assert ("Tests with ids ['" + str(test_case_id)) in str(e.value.detail)


# Guard 3: duplicate detection

def test_raises_409_when_test_already_in_test_set():
    # Both earlier guards pass; scalars returns the test_case_id → 409 for duplicate
    test_set_id = uuid.uuid4()
    test_case_id = uuid.uuid4()

    session = AsyncMock()
    # The 409 check queries TestSetEntryModel.test_id, so the mock returns the UUID directly
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[test_case_id]))

    with patch(_PATCH_FIND_TEST_SET), \
        patch(_PATCH_FIND_TESTS), \
        pytest.raises(HTTPException) as e:
        asyncio.run(add_tests_to_test_set_by_test_id(test_set_id, [
            TestCaseID(id=test_case_id),], session))

    session.commit.assert_not_called()
    assert e.value.status_code == 409
    assert ("Tests with ID '['" + str(test_case_id)) in str(e.value.detail)
    assert str(test_set_id) in str(e.value.detail)


# Happy path

def test_returns_correct_entry_ids():
    # Two valid tests → response contains exactly two TestSetEntryID objects with UUIDs.
    # _find_all_tests_with_details_or_404 is not patched so session.scalars drives it directly.
    test1 = _make_test(name="Test 1")
    test2 = _make_test(name="Test 2")
    test_set_id = uuid.uuid4()
    session = AsyncMock()
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[test1, test2]))

    with patch(_PATCH_FIND_TEST_SET), \
         patch(_PATCH_CHECK_409):
        result = asyncio.run(add_tests_to_test_set_by_test_id(
            test_set_id, [TestCaseID(id=test1.id), TestCaseID(id=test2.id)], session
        ))

    assert len(result) == 2
    assert all(isinstance(entry.id, uuid.UUID) for entry in result)


def test_snapshot_fields_copied_correctly():
    # All snapshot fields (name, input, expected_output, model_output, test_id, test_set_id)
    # must be copied verbatim from the source test onto the persisted entry
    test = _make_test(name="My Test", input_val="the prompt",
                      expected_output="expected", model_output="actual")
    test_set_id = uuid.uuid4()
    session = AsyncMock()

    with patch(_PATCH_FIND_TEST_SET), \
         patch(_PATCH_FIND_TESTS, new=AsyncMock(return_value=[test])), \
         patch(_PATCH_CHECK_409):
        asyncio.run(add_tests_to_test_set_by_test_id(
            test_set_id, [TestCaseID(id=test.id)], session
        ))

    entry = session.add_all.call_args[0][0][0]
    assert entry.name == test.name
    assert entry.input == test.input
    assert entry.expected_output == test.expected_output
    assert entry.model_output == test.model_output
    assert entry.test_id == test.id
    assert entry.test_set_id == test_set_id


def test_type_assignments_extracted_correctly():
    # test_type_assignments must be built from the live test's own
    # test_type_assignments relationship (name + config), not stored UUIDs
    test = _make_test(
        type_names=["Regex Match", "Hallucination"],
        type_configs={"Regex Match": {"pattern": "^\\d+$"}},
    )
    test_set_id = uuid.uuid4()
    session = AsyncMock()

    with patch(_PATCH_FIND_TEST_SET), \
         patch(_PATCH_FIND_TESTS, new=AsyncMock(return_value=[test])), \
         patch(_PATCH_CHECK_409):
        asyncio.run(add_tests_to_test_set_by_test_id(
            test_set_id, [TestCaseID(id=test.id)], session
        ))

    entry = session.add_all.call_args[0][0][0]
    assert entry.test_type_assignments == [
        {"name": "Regex Match", "label": "Regex Match", "config": {"pattern": "^\\d+$"},
         "answer_path": None},
        {"name": "Hallucination", "label": "Hallucination", "config": None,
         "answer_path": None},
    ]


def test_session_add_all_called_with_orm_models():
    # session.add_all must receive TestSetEntryModel instances, not Pydantic schema objects
    test = _make_test()
    test_set_id = uuid.uuid4()
    session = AsyncMock()

    with patch(_PATCH_FIND_TEST_SET), \
         patch(_PATCH_FIND_TESTS, new=AsyncMock(return_value=[test])), \
         patch(_PATCH_CHECK_409):
        asyncio.run(add_tests_to_test_set_by_test_id(
            test_set_id, [TestCaseID(id=test.id)], session
        ))

    session.add_all.assert_called_once()
    added = session.add_all.call_args[0][0]
    assert all(isinstance(entry, TestSetEntryModel) for entry in added)


def test_session_commit_called():
    # commit must be called after add_all to persist the entries
    test = _make_test()
    test_set_id = uuid.uuid4()
    session = AsyncMock()

    with patch(_PATCH_FIND_TEST_SET), \
         patch(_PATCH_FIND_TESTS, new=AsyncMock(return_value=[test])), \
         patch(_PATCH_CHECK_409):
        asyncio.run(add_tests_to_test_set_by_test_id(
            test_set_id, [TestCaseID(id=test.id)], session
        ))

    session.commit.assert_called_once()
