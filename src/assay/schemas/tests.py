import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from assay.models import ConfigFieldKind, TestTypes, TestTypesCost
from assay.schemas import DataSetID, Pagination


class TestTypeAssignment(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"name": "Regex Match", "config": {"pattern": "^\\d{3}-\\d{4}$"}}
        }
    )
    name: str = Field(
        ...,
        description="Name of the test type to assign (must exist in test_types table).",
        examples=["Regex Match"],
    )
    config: dict[str, str] | None = Field(
        None,
        description="Per-field config values, keyed by the type's config_fields[].key. "
                    "Omit or null if the type has no non-reference config fields.",
        examples=[{"pattern": "^\\d{3}-\\d{4}$"}],
    )


class CreateTestCaseRequest(BaseModel):
    name: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Name of test case",
    )
    input: str = Field(
        ...,
        description="Input of test case",
    )
    expected_output: str | None = Field(
        None,
        description="Expected output of test case",
    )
    model_output: str | None = Field(
        None,
        description="The real output of the model",
    )
    test_type_assignments: list[TestTypeAssignment] = Field(
        default_factory=list,
        description="Test types to assign to this test case, with any config "
                    "each one needs.",
    )


class CreateTestCaseFromDatasetRequest(DataSetID):
    test_type_assignments: list[TestTypeAssignment] = Field(
        default_factory=list,
        description="Test types to assign to this test case, with any config "
                    "each one needs.",
    )


class TestCaseID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description="ID of test case",
    )


class TestCaseSnapshotDate(BaseModel):
    snapshot_at: datetime = Field(
        ...,
        description="When this entry was snapshotted from its live test",
    )


class ModifyTestCaseRequest(BaseModel):
    # reject unknown fields — prevents silently ignoring a misplaced id or typos
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(
        None,
        description="Name of test case",
    )
    input: str | None = Field(
        None,
        description="Input of test case",
    )
    expected_output: str | None = Field(
        None,
        description="Expected output of test case",
    )
    model_output: str | None = Field(
        None,
        description="The real output of the model",
    )
    test_type_assignments: list[TestTypeAssignment] | None = Field(
        None,
        description="Test types to assign to this test case, with any config "
                    "each one needs. Replaces the full assignment list — not merged "
                    "with what's already assigned.",
    )


class CreateTestCaseResponse(TestCaseID, CreateTestCaseRequest):
    pass


class CreateTestCaseFromDatasetResponse(DataSetID):
    test_cases: list[TestCaseID]


class PaginatedTestCases(Pagination):
    test_cases: list[CreateTestCaseResponse]


class ConfigFieldDescriptor(BaseModel):
    key: str = Field(
        description="Property name this field's value is stored/read under."
    )
    label: str = Field(
        description="Human-readable label for this field."
    )
    kind: ConfigFieldKind = Field(
        description="What kind of value this field holds. `reference` is reserved — "
                    "it resolves to the test case's own expected_output rather than "
                    "being stored per assignment."
    )
    required: bool = Field(
        description="Whether this field must be filled in when the type is assigned."
    )


class TestTypesSchema(BaseModel):
    id: uuid.UUID = Field(
        description="Unique identifier of the test type catalogue entry."
    )
    name: str = Field(
        description="Unique, stable name used to reference this test type "
                    "(e.g. in test_type_assignments) when assigning it to a test."
    )
    category: TestTypes = Field(
        description="Broad classification of the evaluation strategy: "
                    "deterministic check, NLP metric, or LLM-as-judge."
    )
    description: str | None = Field(
        description="Human-readable explanation of what this test type measures "
                    "and how it works."
    )
    is_active: bool = Field(
        description="Whether this test type is currently available for assignment. "
                    "Inactive entries are kept for historical reference, not for new use."
    )
    created_at: datetime | None = Field(
        description="When this test type was added to the catalogue."
    )
    best_for: str | None = Field(
        description="Guidance on the kinds of tasks or scenarios this test type "
                    "is best suited for."
    )
    cost: TestTypesCost | None = Field(
        description="Relative cost/speed tier of running this test type."
    )
    limitations: str | None = Field(
        description="Known weaknesses or caveats to keep in mind when relying "
                    "on this test type."
    )
    config_fields: list[ConfigFieldDescriptor] = Field(
        description="Config fields this test type needs when assigned, if any. "
                    "Empty for a self-contained type that needs no extra input."
    )
