import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from assay.models import Comparison, ConfigFieldKind, TestTypes, TestTypesCost
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
        description=(
            "When this frozen copy was taken from its live test — when the "
            "test was added to its set, for a test set entry; when the run "
            "was created, for a standalone run"
        ),
    )


class ModifyTestCaseRequest(BaseModel):
    # reject unknown fields — prevents silently ignoring a misplaced id or typos
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(
        None,
        description="Name of test case. It can't be empty: null or left out, it's "
                    "unchanged.",
    )
    input: str | None = Field(
        None,
        description="Input of test case. It can't be empty: null or left out, it's "
                    "unchanged.",
    )
    expected_output: str | None = Field(
        None,
        description="Expected output of test case. Send null to clear it; leave the key "
                    "out to keep it.",
    )
    model_output: str | None = Field(
        None,
        description="The real output of the model. Send null to clear it — runs then ask "
                    "the application under test for the answer; leave the key out to "
                    "keep it.",
    )
    test_type_assignments: list[TestTypeAssignment] | None = Field(
        None,
        description="Test types to assign to this test case, with any config "
                    "each one needs. Replaces the full assignment list — not merged "
                    "with what's already assigned. null or left out: unchanged.",
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
    min: float | None = Field(
        None,
        description="Minimum allowed value, for `kind: \"numeric\"` fields only. "
                    "Null for every other kind. Advisory only — the API does not "
                    "enforce this against submitted config values; it exists so "
                    "the FE doesn't have to hardcode bounds per type.",
    )
    max: float | None = Field(
        None,
        description="Maximum allowed value, for `kind: \"numeric\"` fields only. "
                    "Null for every other kind. Advisory only, same as `min`.",
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
                    "Empty for a self-contained type that needs no extra input. A "
                    "`numeric` field's `min`/`max` are that type's own native score "
                    "range (e.g. 0–1 for ROUGE, 0–100 for BLEU, −1 to 1 for Cosine "
                    "Similarity), so a `threshold` is always written on the scale the "
                    "metric itself reports."
    )
    engine: str = Field(
        description="Read-only. Which evaluator engine scores this type in the worker "
                    "(e.g. `exact_match`, `rouge`, `llm_judge`). Several types can "
                    "share one engine and differ only in `engine_settings`."
    )
    engine_settings: dict = Field(
        description="Read-only. The engine's parameters for this type — shape depends "
                    "on the engine (e.g. `{\"variant\": \"rougeL\"}` for ROUGE, "
                    "`{\"default_rubric\": \"...\"}` for an LLM-judge type). Empty "
                    "for an engine that takes nothing."
    )
    comparison: Comparison | None = Field(
        description="Read-only. For a type scored against its `threshold` field, which "
                    "way it passes: `gte` (score ≥ threshold, higher is better) or "
                    "`lte` (score ≤ threshold, lower is better). Null for a type that "
                    "isn't scored against a threshold (deterministic checks, LLM "
                    "judges)."
    )
