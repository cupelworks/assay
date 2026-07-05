import uuid

from pydantic import BaseModel, Field


class TestSetEntryID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description='The unique identifier of the test set entry',
    )
