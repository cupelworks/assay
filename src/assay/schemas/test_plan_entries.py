# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import uuid

from pydantic import BaseModel, Field

from assay.schemas._common import Pagination
from assay.schemas.test_sets import TestSetMetadata


class TestPlanEntryID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description='The unique identifier of the test plan entry',
    )
    

class TestPlanEntryDetails(TestPlanEntryID):
    test_set: TestSetMetadata = Field(
        ...,
        description='The test set this entry links to the test plan',
    )


class PaginatedTestPlanEntriesDetails(Pagination):
    items: list[TestPlanEntryDetails]
