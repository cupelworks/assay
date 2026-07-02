import uuid

from pydantic import BaseModel, Field


class TestSetName(BaseModel):
    name: str = Field(
        ...,
        description="The name of the test set",
        min_length=1,
    )


class TestSetID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description="The ID of the test set",
    )


class TestSetCreationResponse(TestSetID, TestSetName):
    pass
