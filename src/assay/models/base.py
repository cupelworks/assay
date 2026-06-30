from pydantic import BaseModel, Field
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base — all ORM models inherit from this so
    Base.metadata holds every table definition (needed by Alembic autogenerate)."""


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