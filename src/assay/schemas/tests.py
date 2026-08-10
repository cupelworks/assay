import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from assay.schemas import DataSetID, Pagination


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
    test_type_names: list[str] = Field(
        default_factory=list,
        description="Names of test types to assign to "
                    "this test case (must exist in test_types table).",
    )


class CreateTestCaseFromDatasetRequest(DataSetID):
    test_type_names: list[str] = Field(
        default_factory=list,
        description="Names of test types to assign to "
        "this test case (must exist in test_types table).",
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
    test_type_names: list[str] | None = Field(
        None,
        description="Names of test types to assign to "
                    "this test case (must exist in test_types table).",
    )


class CreateTestCaseResponse(TestCaseID, CreateTestCaseRequest):
    pass


class CreateTestCaseFromDatasetResponse(DataSetID):
    test_cases: list[TestCaseID]


class PaginatedTestCases(Pagination):
    test_cases: list[CreateTestCaseResponse]
