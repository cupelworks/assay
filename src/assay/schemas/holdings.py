# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
"""Which test sets hold a copy of a test, and which test plans link a test
set: the reverse of a set's entries and a plan's linked sets."""
import uuid

from pydantic import BaseModel, Field

from assay.schemas._common import Pagination
from assay.schemas.test_plans import TestPlanMetadata
from assay.schemas.test_sets import TestSetMetadata


class EntryCopy(BaseModel):
    """A set entry copied from a test."""
    id: uuid.UUID
    has_runs: bool = Field(description="Whether it has run: then it's frozen.")
    matches_test: bool = Field(
        description="Whether it still asks what the test asks now: the same input, expected "
                    "answer, recorded answer and checks. The name isn't compared.")


class TestSetHolding(BaseModel):
    test_set: TestSetMetadata
    entry: EntryCopy


class PaginatedTestSetHoldings(Pagination):
    items: list[TestSetHolding]
    unlinked_copies: int = Field(
        description="Copies of the test whose set link was removed: still kept, they belong "
                    "to no set.")


class TestPlanLink(BaseModel):
    test_plan: TestPlanMetadata
    entry_id: uuid.UUID = Field(description="The plan's entry that links the test set.")


class PaginatedTestPlanLinks(Pagination):
    items: list[TestPlanLink]
