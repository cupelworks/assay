import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from assay.schemas import Pagination
from assay.schemas.standing import ScopeStanding


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


class TestSetMetadata(TestSetID, TestSetName, ScopeStanding):
    test_plan_count: int = Field(
        description="How many test plans link it (`GET /test-sets/{id}/test-plans`).")
    created_at: datetime = Field(
        ...,
        description="The creation date of the test set.",
    )
    entry_count: int = Field(
        ...,
        description="The number of entries currently in the test set.",
    )


class PaginatedTestSetMetadataResponse(Pagination):
    items: list[TestSetMetadata]


class ModifyTestSetMetadataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(
        ...,
        description="The name of the test set",
        min_length=1,
    )
    
    
class TestSetCreationResponse(TestSetID, TestSetName):
    pass
