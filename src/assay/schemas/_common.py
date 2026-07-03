from pydantic import BaseModel, Field


class Pagination(BaseModel):
    total: int = Field(
        ...,
        description="The total number of datasets in the database.",
    )
    offset: int = Field(
        ...,
        description="Number of records to skip for pagination."
    )
    limit: int = Field(
        ...,
        description="The maximum number of records to return for pagination.",
    )
