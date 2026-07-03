import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from assay.schemas import Pagination


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


class TestSetMetadata(TestSetID, TestSetName):
    created_at: datetime = Field(
        ...,
        description="The creation date of the test set.",
    )


class PaginatedTestSetMetadataResponse(Pagination):
    items: list[TestSetMetadata]


class TestSetCreationResponse(TestSetID, TestSetName):
    pass
