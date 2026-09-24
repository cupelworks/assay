import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from assay.models import TestSetEntryModel, TestSetModel
from assay.schemas import TestTypeAssignment
from assay.services import modify_entry_by_id

_PATCH_FIND_TEST_SET_OR_404 = "assay.services.test_sets.update_entry._find_test_set_or_404"
_PATCH_FIND_TEST_SET_ENTRY_IN_SPECIFIC_TEST_SET_OR_404 = \
    "assay.services.test_sets.update_entry._find_test_set_entry_in_specific_test_set_or_404"
_PATCH_CHECK_TEST_SET_ENTRY_HAS_NO_RUNS_OR_409 = \
    "assay.services.test_sets.update_entry._check_test_set_entry_has_no_runs_or_409"
_PATCH_APPLY_SCALAR_UPDATES = "assay.services.test_sets.update_entry._apply_scalar_updates"


def test_test_set_not_found():
    session = AsyncMock()
    session.scalar.return_value = None

    test_set_id = uuid.uuid4()

    with pytest.raises(HTTPException) as e:
        asyncio.run(
            modify_entry_by_id(
                test_set_id, uuid.uuid4(), MagicMock(), session)
        )

    assert e.value.status_code == 404
    assert f"Test set with ID '{test_set_id}" in str(e.value.detail)
    assert session.scalar.call_count == 1


def test_test_set_entry_not_found_in_specific_test_set():
    test_set_id = uuid.uuid4()
    entry_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [TestSetModel(
        id=test_set_id,
        name="Test set",
        created_at=datetime.now(),
    ), None]

    with pytest.raises(HTTPException) as e:
        asyncio.run(
            modify_entry_by_id(
                test_set_id, entry_id, MagicMock(), session
            )
        )

    assert e.value.status_code == 404
    assert f"Test entry with ID '{entry_id}' not found" in str(e.value.detail)
    assert f"in test set with ID '{test_set_id}'" in str(e.value.detail)
    assert session.scalar.call_count == 2


def test_test_set_entry_has_runs():
    test_set_id = uuid.uuid4()
    entry_id = uuid.uuid4()

    session = AsyncMock()
    session.scalar.side_effect = [TestSetModel(
        id=test_set_id,
        name="Test set",
        created_at=datetime.now(),
    ), TestSetEntryModel(
        id=entry_id,
    ), uuid.uuid4()]

    with pytest.raises(HTTPException) as e:
        asyncio.run(
            modify_entry_by_id(
                test_set_id, entry_id, MagicMock(), session
            )
        )

    assert e.value.status_code == 409
    assert f"Test entry with ID '{entry_id}' can't be modified" in str(e.value.detail)
    assert session.scalar.call_count == 3


def test_invalid_test_type_assignment():
    request = MagicMock()
    request.test_type_assignments = [TestTypeAssignment(name="Testing wrong name")]

    session = AsyncMock()
    catalogue_row = MagicMock()
    catalogue_row.name = "ROUGE"
    catalogue_row.config_fields = [
        {"key": "reference", "label": "Reference text", "kind": "reference", "required": True}
    ]
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[catalogue_row]))

    with patch(_PATCH_FIND_TEST_SET_OR_404), \
            patch(_PATCH_FIND_TEST_SET_ENTRY_IN_SPECIFIC_TEST_SET_OR_404), \
            patch(_PATCH_CHECK_TEST_SET_ENTRY_HAS_NO_RUNS_OR_409), \
            patch(_PATCH_APPLY_SCALAR_UPDATES), \
            pytest.raises(HTTPException) as e:
        asyncio.run(
            modify_entry_by_id(
                uuid.uuid4(), uuid.uuid4(), request, session
            )
        )

    assert e.value.status_code == 422
    assert request.test_type_assignments[0].name in str(e.value.detail)


def test_clearing_test_type_assignments():
    request = MagicMock()
    request.test_type_assignments = []

    session = AsyncMock()
    session.scalar.return_value = TestSetEntryModel(
        id=uuid.uuid4(),
        test_id=uuid.uuid4(),
        test_type_assignments=[{"name": "ROUGE", "config": None}],
        name="",
        input="",
        expected_output="",
        model_output="",
    )
    session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))

    with patch(_PATCH_FIND_TEST_SET_OR_404), \
            patch(_PATCH_CHECK_TEST_SET_ENTRY_HAS_NO_RUNS_OR_409), \
            patch(_PATCH_APPLY_SCALAR_UPDATES):
        response = asyncio.run(
            modify_entry_by_id(
                uuid.uuid4(), uuid.uuid4(), request, session
            )
        )

    assert response.test_type_assignments == []
    session.commit.assert_called_once()


def test_general_happy_path_with_test_type_assignments_on_none():
    entry_id = uuid.uuid4()

    request = MagicMock()
    request.test_type_assignments = None
    request.name = "New name"
    request.input = "New input"
    request.expected_output = None
    request.model_output = None

    session = AsyncMock()
    session.scalar.return_value = TestSetEntryModel(
        id=entry_id,
        test_id=uuid.uuid4(),
        test_type_assignments=[{"name": "ROUGE", "config": None}],
        name="Old name",
        input="Old input",
        expected_output="Old expected output",
        model_output="Old model output",
    )

    with patch(_PATCH_FIND_TEST_SET_OR_404), \
            patch(_PATCH_CHECK_TEST_SET_ENTRY_HAS_NO_RUNS_OR_409):
        response = asyncio.run(
            modify_entry_by_id(
                uuid.uuid4(), entry_id, request, session
            )
        )

    assert response.id == entry_id
    assert response.test_type_assignments == [TestTypeAssignment(name="ROUGE", config=None)]
    assert response.name == request.name
    assert response.input == request.input
    assert response.model_output == "Old model output"
    assert response.expected_output == "Old expected output"
    session.commit.assert_called_once()
    session.execute.assert_not_called()
