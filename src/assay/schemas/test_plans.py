import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from assay.schemas import Pagination
from assay.schemas.standing import ScopeStanding


class TestPlanName(BaseModel):
    name: str = Field(
        ...,
        description="The name of the test plan",
        min_length=1,
    )


class TestPlanID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description="The ID of the test plan",
    )


class TestPlanMetadata(TestPlanID, TestPlanName, ScopeStanding):
    created_at: datetime = Field(
        ...,
        description="The creation date of the test plan.",
    )
    linked_set_count: int = Field(
        ...,
        description="The number of test sets currently linked to the test plan.",
    )


class ModifyTestPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(
        ...,
        description="The name of the test plan",
        min_length=1,
    )


class PaginatedTestPlanMetadataResponse(Pagination):
    items: list[TestPlanMetadata]


class TestPlanCreationResponse(TestPlanID, TestPlanName):
    pass
