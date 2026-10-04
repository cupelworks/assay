"""How a test, test set or test plan stands, read on its list without opening
it: its latest batch, its latest run or execution outside a batch, and
whether it has runs (what makes it impossible to delete, or an entry
frozen)."""
import uuid

from pydantic import BaseModel, Field

from assay.models import TestStatus
from assay.schemas._common import RunCounts
from assay.schemas.statistics import BatchStatusName
from assay.timestamps import Timestamp


class LatestBatch(BaseModel):
    id: uuid.UUID
    status: BatchStatusName
    created_at: Timestamp


class LatestRun(BaseModel):
    id: uuid.UUID
    status: TestStatus
    created_at: Timestamp


class LatestExecution(BaseModel):
    id: uuid.UUID
    created_at: Timestamp
    replayed: bool = Field(description="Whether it replayed an earlier execution's entries.")
    runs: RunCounts = Field(description="Its runs by status, every status present.")


class ScopeStanding(BaseModel):
    """How a test set or test plan stands."""
    latest_batch: LatestBatch | None = Field(
        description="Its newest statistical batch, or null when it never ran with statistics.")
    latest_execution: LatestExecution | None = Field(
        description="Its newest execution outside a batch (live or replay), or null.")
    execution_count: int = Field(
        description="How many times it ran outside a batch, live runs and replays, whatever "
                    "their runs' statuses.")
    has_runs: bool = Field(
        description="Whether anything ever ran it, a batch included: then it can't be "
                    "deleted.")


class TestStanding(BaseModel):
    """How a test stands, and where it came from."""
    created_at: Timestamp
    dataset_row_id: uuid.UUID | None = Field(
        description="The dataset row it was made from, or null (made by hand, or its row "
                    "was deleted or replaced since).")
    dataset_row_number: int | None = Field(
        description="That row's number within its dataset, as its rows list shows it.")
    latest_batch: LatestBatch | None = Field(
        description="Its newest statistical batch, or null.")
    latest_run: LatestRun | None = Field(
        description="Its newest standalone run outside a batch, or null.")
    has_runs: bool = Field(
        description="Whether it has any standalone run, a batch's included: then it can't "
                    "be deleted.")
    copy_count: int = Field(
        description="How many set entries were copied from it, entries since unlinked from "
                    "their set included: while any exists, it can't be deleted.")
    test_set_count: int = Field(
        description="How many test sets hold a copy of it now (`GET /tests/{id}/test-sets`).")
