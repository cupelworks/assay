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
    has_runs: bool = Field(
        description="Whether the entry has ever run, a batch included: then it's frozen, and "
                    "can't be edited or deleted.",
    )


class PaginatedTestSetEntriesDetails(Pagination):
    items: list[TestSetEntryDetails]
