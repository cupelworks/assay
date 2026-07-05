import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.models import TestSetEntryModel
from assay.services import get_test_set_linked_test_by_entry_id, get_test_sets_linked_tests

_PATCH_FIND_TEST_SET = "assay.services.test_sets.get_test_sets_entries._find_test_set_or_404"

# --- get_test_sets_linked_tests() ---

def test_test_set_not_found():
    test_set_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with pytest.raises(HTTPException) as e:
        asyncio.run(get_test_sets_linked_tests(test_set_id, session))

    session.scalars.assert_not_called()
    assert e.value.status_code == 404
    assert str(test_set_id) in str(e.value.detail)


def test_returns_correct_items():
    test_set_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalar.return_value = 2

    first_test_set_entry_id = uuid.uuid4()
    first_test_id = uuid.uuid4()
    first_name = "First Name"
    first_input = "First Input"
    first_expected_output = "First Expected Output"
    first_model_output = "First Model Output"
    first_test_type_names = ["semantic_similarity", "toxicity"]

    second_test_set_entry_id = uuid.uuid4()
    second_test_id = uuid.uuid4()
    second_name = "Second Name"
    second_input = "Second Input"
    second_expected_output = "Second Expected Output"
    second_model_output = None
    second_test_type_names = ["exact_match"]

    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[
        TestSetEntryModel(
            id=first_test_set_entry_id,
            test_set_id=test_set_id,
            test_id=first_test_id,
            name=first_name,
            input=first_input,
            expected_output=first_expected_output,
            model_output=first_model_output,
            test_type_names=first_test_type_names,
        ),
        TestSetEntryModel(
            id=second_test_set_entry_id,
            test_set_id=test_set_id,
            test_id=second_test_id,
            name=second_name,
            input=second_input,
            expected_output=second_expected_output,
            model_output=second_model_output,
            test_type_names=second_test_type_names,
        )
    ]))
    
    with patch(_PATCH_FIND_TEST_SET):
        response = asyncio.run(get_test_sets_linked_tests(test_set_id, session))

    assert response.total == 2
    assert response.offset == 0
    assert response.limit == 100
    assert len(response.items) == 2

    assert response.items[0].id == first_test_set_entry_id
    assert response.items[0].test_case_id.id == first_test_id
    assert response.items[0].name == first_name
    assert response.items[0].input == first_input
    assert response.items[0].expected_output == first_expected_output
    assert response.items[0].model_output == first_model_output
    assert response.items[0].test_type_names == first_test_type_names

    assert response.items[1].id == second_test_set_entry_id
    assert response.items[1].test_case_id.id == second_test_id
    assert response.items[1].name == second_name
    assert response.items[1].input == second_input
    assert response.items[1].expected_output == second_expected_output
    assert response.items[1].model_output is None
    assert response.items[1].test_type_names == second_test_type_names

    
def test_empty_test_set():
    session = AsyncMock()

    session.scalar.return_value = 0
    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    with patch(_PATCH_FIND_TEST_SET):
        response = asyncio.run(get_test_sets_linked_tests(uuid.uuid4(), session,
                                                          offset=3, limit=50))

    assert len(response.items) == 0
    assert response.total == 0
    assert response.offset == 3
    assert response.limit == 50


# --- get_test_set_linked_test_by_entry_id() ---
def test_test_set_not_found_by_entry_id():
    test_set_id = uuid.uuid4()
    
    session = AsyncMock()
    session.scalar.return_value = None
    
    with pytest.raises(HTTPException) as e:
        asyncio.run(get_test_set_linked_test_by_entry_id(test_set_id, uuid.uuid4(), session))

    assert session.scalar.call_count == 1
    assert e.value.status_code == 404
    assert ("Test set with ID '" + str(test_set_id)) in str(e.value.detail)


def test_entry_not_found_in_test_set():
    test_set_id = uuid.uuid4()
    entry_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.return_value = None

    with patch(_PATCH_FIND_TEST_SET), pytest.raises(HTTPException) as e:
        asyncio.run(get_test_set_linked_test_by_entry_id(test_set_id, entry_id, session))

    assert e.value.status_code == 404
    assert (f"Test entry with ID '{entry_id}' not found "
            f"in test set with ID '{test_set_id}'") in str(e.value.detail)


def test_returns_correct_entry():
    test_set_id = uuid.uuid4()
    test_case_id = uuid.uuid4()
    entry_id = uuid.uuid4()

    found_name = "Name"
    found_input = "Input"
    found_expected_output = None
    found_model_output = "Model Output"
    found_test_type_names = ["Exact Match", "ROUGE"]

    session = AsyncMock()
    session.scalar.return_value = TestSetEntryModel(
        id=entry_id,
        test_set_id=test_set_id,
        test_id=test_case_id,
        name=found_name,
        input=found_input,
        expected_output=found_expected_output,
        model_output=found_model_output,
        test_type_names=found_test_type_names,
    )

    with patch(_PATCH_FIND_TEST_SET):
        response = asyncio.run(get_test_set_linked_test_by_entry_id(test_set_id, entry_id, session))

    assert response.id == entry_id
    assert response.test_case_id.id == test_case_id
    assert response.name == found_name
    assert response.input == found_input
    assert response.expected_output is None
    assert response.model_output == found_model_output
    assert response.test_type_names == found_test_type_names

