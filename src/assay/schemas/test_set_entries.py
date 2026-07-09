import uuid

from pydantic import BaseModel, Field

from assay.schemas import CreateTestCaseRequest, Pagination, TestCaseID


class TestSetEntryID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description='The unique identifier of the test set entry',
    )
    

class TestSetEntryDetails(TestSetEntryID, CreateTestCaseRequest):
    test_case_id: TestCaseID = Field(
        ...,
        description='The unique identifier of the test this entry was created from',
    )


class PaginatedTestSetEntriesDetails(Pagination):
    items: list[TestSetEntryDetails]
