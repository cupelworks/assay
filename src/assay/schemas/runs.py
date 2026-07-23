import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from assay.models import TestStatus
from assay.schemas import TestCaseID, TestPlanID, TestSetID


class RunID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description='Unique identifier of the run (`TestRunModel.id`).',
    )


class RunStatus(BaseModel):
    status: TestStatus = Field(
        ...,
        description=(
            'Current lifecycle status of the run: `Pending` (created but not '
            'yet executed), `Running`, `Completed`, or `Failed`. A newly '
            'created run is always `Pending`'
        ),
    )


class RunCreationDate(BaseModel):
    created_at: datetime = Field(
        ...,
        description=(
            'Timestamp when the run was created (enqueued as `Pending`), not '
            'when it started or finished executing.'
        ),
    )


class StandaloneRunCreationMetadata(RunID, RunStatus, RunCreationDate):
    test_case_id: TestCaseID = Field(
        ...,
        description='ID of the live test this standalone run was created for.',
    )


class TestSetExecutionID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description=(
            'Unique identifier of the test set execution (`TestSetExecutionModel.id`) '
            '— the trigger-event record grouping every run produced by the same '
            'live fan-out or replay.'
        ),
    )


class TestSetExecutionCreationDate(BaseModel):
    created_at: datetime = Field(
        ...,
        description=(
            'Timestamp when the test set run was created '
            '(when its entries got enqueued as `Pending`), not '
            'when it started or finished executing.'
        ),
    )


class TestSetLiveRunCreationMetadata(TestSetExecutionID, TestSetExecutionCreationDate):
    test_set_id: TestSetID
    run_count: int = Field(
        ...,
        description=(
            'Number of TestRunModel rows created by this execution — one per '
            'entry that was in the test set at the moment this live execution '
            'was triggered.'
        ),
    )


class TestSetReplayedExecutionID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description=(
            'Unique identifier of the prior test set execution '
            '(`TestSetExecutionModel.replayed_execution_id`) that this execution '
            'replayed — its new runs target the exact same test set entries that '
            'execution used, rather than fanning out fresh from the set\'s '
            'current entries.'
        ),
    )


class TestSetReplayedExecutionCreationMetadata(TestSetLiveRunCreationMetadata):
    replayed_execution_id: TestSetReplayedExecutionID
    run_count: int = Field(
        ...,
        description=(
            'Number of TestRunModel rows created by this replay — always equal '
            'to the number of runs `replayed_execution_id` produced, since '
            'replay re-targets the exact same test set entries rather than '
            'fanning out fresh.'
        ),
    )


class TestPlanExecutionID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description=(
            'Unique identifier of the test plan execution (`TestPlanExecutionModel.id`) '
            '— the trigger-event record grouping every run produced by the same '
            'live fan-out or replay.'
        ),
    )


class TestPlanExecutionCreationDate(BaseModel):
    created_at: datetime = Field(
        ...,
        description=(
            'Timestamp when the test plan run was created '
            '(when its entries got enqueued as `Pending`), not '
            'when it started or finished executing.'
        ),
    )


class TestPlanLiveRunCreationMetadata(TestPlanExecutionID, TestPlanExecutionCreationDate):
    test_plan_id: TestPlanID
    run_count: int = Field(
        ...,
        description=(
            "Number of TestRunModel rows created by this execution — one per "
            "entry across all of the plan's linked test sets at the moment "
            "this live execution was triggered."
        ),
    )


class TestPlanReplayedExecutionID(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description=(
            "Unique identifier of the prior test plan execution "
            "(`TestPlanExecutionModel.replayed_execution_id`) that this execution "
            "replayed — its new runs target the exact same test set entries that "
            "execution used, rather than fanning out fresh from the plan's "
            "currently linked test sets."
        )
    )


class TestPlanReplayedExecutionCreationMetadata(TestPlanLiveRunCreationMetadata):
    replayed_execution_id: TestPlanReplayedExecutionID
    run_count: int = Field(
        ...,
        description=(
            "Number of TestRunModel rows created by this replay — always equal "
            "to the number of runs `replayed_execution_id` produced, since "
            "replay re-targets the exact same test set entries rather than "
            "fanning out fresh."
        )
    )
