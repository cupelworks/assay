import uuid

from pydantic import BaseModel, Field


class CreateTestCaseRequest(BaseModel):
    name: str | None = Field(
        None,
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


class TestCaseID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description="ID of test case",
    )


class CreateTestCaseResponse(TestCaseID, CreateTestCaseRequest):
    pass
