import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from assay.models import Comparison, ConfigFieldKind, TestTypes, TestTypesCost
from assay.schemas import DataSetID, Pagination

MAX_LABEL_LENGTH = 100


class TestTypeAssignment(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"name": "Contains", "label": "Mentions the order number",
                        "config": {"substring": "4471"}}
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
    answer_path: str | None = Field(
        None,
        description="Which part of the answer this check reads, as a JSONPath: into the "
                    "application's whole reply (e.g. `$.stop_reason`, `$.output.category`), "
                    "or into a recorded `model_output` that is JSON. Omit or null to read "
                    "the answer at the settings' output path, like every other check. "
                    "Checked to parse on save — a 422 otherwise.",
        examples=["$.stop_reason"],
    )
    label: str | None = Field(
        None,
        max_length=MAX_LABEL_LENGTH,
        description="What this check is called within the test, unique there (ignoring "
                    "letter case): a type can be assigned more than once, and the label "
                    "tells the assignments apart — a run's `results` are keyed by it. "
                    "Optional: left out, it's the type's name, numbered when that's taken "
                    "(`Contains`, `Contains 2`). A label sent is kept as it is, so send "
                    "back the labels a GET returned to keep each check's identity — and "
                    "its results comparable across runs — when you replace the list.",
        examples=["Mentions the order number"],
    )

    @field_validator("label", mode="before")
    @classmethod
    def _blank_label_is_unset(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


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
        description="What the answer should be — the reference that types with a "
                    "`reference` field compare against (e.g. Exact Match, ROUGE).",
    )
    model_output: str | None = Field(
        None,
        description="The answer your application gave, if you recorded it: runs score it "
                    "as it is. Null: every run asks the application under test instead.",
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
        description="The answer your application gave, if you recorded it. Send null to "
                    "clear it — runs then ask the application under test for the answer; "
                    "leave the key out to keep it.",
    )
    test_type_assignments: list[TestTypeAssignment] | None = Field(
        None,
        description="Test types to assign to this test case, with any config "
                    "each one needs. Replaces the full assignment list — not merged "
                    "with what's already assigned; `[]` removes them all. null or left "
                    "out: unchanged. A type may appear more than once, told apart by "
                    "`label`: send back the labels a GET returned to keep each check's "
                    "identity. Returned in label order, ignoring letter case.",
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
                    "being stored per assignment. `json` holds JSON text, `jsonpath` a "
                    "JSONPath expression, `regex` a regular expression and "
                    "`json_schema` a JSON Schema written as JSON; `numeric` a number "
                    "within `min`/`max`. Each is checked when the type is assigned, "
                    "and a value that doesn't parse or is out of range is a 422."
    )
    required: bool = Field(
        description="Whether this field must be filled in when the type is assigned."
    )
    min: float | None = Field(
        None,
        description="Minimum allowed value, inclusive, for `kind: \"numeric\"` fields "
                    "only; null for no minimum and for every other kind. A value below "
                    "it is refused with a 422 when the type is assigned.",
    )
    max: float | None = Field(
        None,
        description="Maximum allowed value, inclusive, for `kind: \"numeric\"` fields "
                    "only; null for no maximum and for every other kind. A value above "
                    "it is refused with a 422, like `min`.",
    )
    integer: bool = Field(
        False,
        description="For `kind: \"numeric\"` fields: true when the value must be a whole "
                    "number (e.g. a word limit), refused with a 422 otherwise. False for "
                    "every other field.",
    )
    placeholder: str | None = Field(
        None,
        description="An example value to show in the empty input, e.g. `$.status`. "
                    "Null when the field has none.",
    )
    hint: str | None = Field(
        None,
        description="A one-line note on how to fill the field in, shown under it: what "
                    "the value means and what to expect. It never restates what the "
                    "descriptor already says — the range (`min`/`max`), whether it's a "
                    "whole number (`integer`), the pass direction (the type's "
                    "`comparison`) or whether it's required. Null when the field has "
                    "none.",
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
