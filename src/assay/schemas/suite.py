from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class TestCase(BaseModel):
    """A single input/expected-output pair to exercise the target GenAI app."""

    id: UUID = Field(default_factory=uuid4)
    input: str
    expected_output: str | None = None
    context: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TestSuite(BaseModel):
    """A named collection of test cases."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    description: str | None = None
    cases: list[TestCase]
