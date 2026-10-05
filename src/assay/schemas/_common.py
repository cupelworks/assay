# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
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


class RunCounts(BaseModel):
    """Runs by status: every key always present, 0 when none."""
    Pending: int = 0
    Running: int = 0
    Green: int = 0
    Amber: int = 0
    Red: int = 0
    NotRan: int = 0

    def add(self, status: str, runs: int = 1) -> None:
        """Count `runs` more at `status` (a TestStatus value, e.g. "Green")."""
        setattr(self, status, getattr(self, status) + runs)
