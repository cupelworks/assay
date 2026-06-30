import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from assay.services import get_all_created_tests

# --- get_all_created_tests ---

def test_returns_empty_list_when_no_test_exist():
    session = AsyncMock()

    session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    session.scalar.return_value = None

    test_cases = asyncio.run(get_all_created_tests(session, offset=2, limit=3))

    assert len(test_cases.test_cases) == 0
    assert test_cases.total == 0
    assert test_cases.offset == 2
    assert test_cases.limit == 3


def test_correct_mapping_of_test_fields():
    mock_id = uuid.uuid4()
    mock_test = MagicMock()
    mock_test.id = mock_id
    mock_test.name = "test"
    mock_test.input = "my input"
    mock_test.model_output = "my model output"
    mock_test.expected_output = "my expected output"

    mock_assignment = MagicMock()
    mock_assignment.test_type_name = "ROUGE"

    mock_test.test_type_assignments = [mock_assignment]

    session = AsyncMock()
    session.scalars.return_value = MagicMock(
        all=MagicMock(
            return_value=[mock_test]
        )
    )
    session.scalar.return_value = 1

    test_cases = asyncio.run(get_all_created_tests(session))

    returned_test_case = test_cases.test_cases[0]
    assert returned_test_case.id == mock_id
    assert returned_test_case.name == mock_test.name
    assert returned_test_case.input == mock_test.input
    assert returned_test_case.model_output == mock_test.model_output
    assert returned_test_case.expected_output == mock_test.expected_output
    assert returned_test_case.test_type_names[0] == mock_assignment.test_type_name


def test_no_test_types_returns_empty_list():
    mock_test = MagicMock()
    mock_test.id = uuid.uuid4()
    mock_test.name = "test"
    mock_test.input = "my input"
    mock_test.model_output = "my model output"
    mock_test.expected_output = "my expected output"
    mock_test.test_type_assignments = []

    session = AsyncMock()
    session.scalars.return_value = MagicMock(
        all=MagicMock(
            return_value=[mock_test]
        )
    )
    session.scalar.return_value = 1

    test_cases = asyncio.run(get_all_created_tests(session))

    assert test_cases.test_cases[0].test_type_names == []
