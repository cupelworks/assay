import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from assay.models import TestStatus
from assay.schemas import TestCaseID


class RunID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description='The ID of the Run',
    )


class RunStatus(BaseModel):
    status: TestStatus = Field(
        ...,
        description='The status of the Run',
    )


class RunCreationDate(BaseModel):
    created_at: datetime = Field(
        ...,
        description='The creation date of the Run',
    )


class StandaloneRunCreationMetadata(RunID, RunStatus, RunCreationDate):
    test_case_id: TestCaseID = Field(
        ...,
        description='The test case ID the Run has been created for',
    )
