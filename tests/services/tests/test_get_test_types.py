import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from assay.models import TestTypes, TestTypesCost
from assay.schemas import ConfigFieldDescriptor
from assay.services import get_test_types_by_category

# --- get_test_types_by_category ---


def test_returns_empty_list_when_no_test_types_in_category():
    session = AsyncMock()
    session.scalars.return_value = []

    result = asyncio.run(get_test_types_by_category(TestTypes.llm_as_judge, session))

    assert result == []


def test_correct_mapping_of_test_type_fields():
    mock_id = uuid.uuid4()
    mock_created_at = datetime.now().astimezone()
    mock_test_type = MagicMock()
    mock_test_type.id = mock_id
    mock_test_type.name = "ROUGE"
    mock_test_type.category = TestTypes.nlp_metric
    mock_test_type.description = "Measures n-gram overlap between output and expected text."
    mock_test_type.is_active = True
    mock_test_type.created_at = mock_created_at
    mock_test_type.best_for = "Summarization tasks."
    mock_test_type.cost = TestTypesCost.fast
    mock_test_type.limitations = "Doesn't account for semantic meaning."
    mock_test_type.config_fields = [
        {"key": "reference", "label": "Reference text", "kind": "reference", "required": True}
    ]

    session = AsyncMock()
    session.scalars.return_value = [mock_test_type]

    result = asyncio.run(get_test_types_by_category(TestTypes.nlp_metric, session))

    assert len(result) == 1
    returned = result[0]
    assert returned.id == mock_id
    assert returned.name == "ROUGE"
    assert returned.category == TestTypes.nlp_metric
    assert returned.description == mock_test_type.description
    assert returned.is_active is True
    assert returned.created_at == mock_created_at
    assert returned.best_for == mock_test_type.best_for
    assert returned.cost == TestTypesCost.fast
    assert returned.limitations == mock_test_type.limitations
    assert returned.config_fields == [
        ConfigFieldDescriptor(key="reference", label="Reference text",
                              kind="reference", required=True)
    ]


def test_nullable_fields_pass_through_as_none():
    mock_test_type = MagicMock()
    mock_test_type.id = uuid.uuid4()
    mock_test_type.name = "Custom Type"
    mock_test_type.category = TestTypes.deterministic
    mock_test_type.description = None
    mock_test_type.is_active = True
    mock_test_type.created_at = None
    mock_test_type.best_for = None
    mock_test_type.cost = None
    mock_test_type.limitations = None
    # config_fields is never null at the model level (NOT NULL, default=list) —
    # unlike the other fields checked here, there's no nullable case to cover for it.
    mock_test_type.config_fields = []

    session = AsyncMock()
    session.scalars.return_value = [mock_test_type]

    result = asyncio.run(get_test_types_by_category(TestTypes.deterministic, session))

    returned = result[0]
    assert returned.description is None
    assert returned.created_at is None
    assert returned.best_for is None
    assert returned.cost is None
    assert returned.limitations is None
    assert returned.config_fields == []


def _mock_test_type(name: str) -> MagicMock:
    # MagicMock's constructor treats `name=` specially (sets the mock's repr),
    # so .name must be assigned separately rather than passed as a kwarg.
    mock_test_type = MagicMock(
        id=uuid.uuid4(), category=TestTypes.nlp_metric,
        description=None, is_active=True, created_at=None,
        best_for=None, cost=TestTypesCost.fast, limitations=None, config_fields=[],
    )
    mock_test_type.name = name
    return mock_test_type


def test_preserves_order_and_count_returned_by_the_query():
    mock_a = _mock_test_type("ROUGE")
    mock_b = _mock_test_type("METEOR")

    session = AsyncMock()
    session.scalars.return_value = [mock_a, mock_b]

    result = asyncio.run(get_test_types_by_category(TestTypes.nlp_metric, session))

    assert [test_type.name for test_type in result] == ["ROUGE", "METEOR"]
