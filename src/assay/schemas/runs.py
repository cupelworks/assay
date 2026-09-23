import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from assay.models import TestStatus
from assay.schemas import (
    CreateTestCaseRequest,
    Pagination,
    TestCaseID,
    TestCaseSnapshotDate,
    TestPlanID,
    TestSetEntryID,
    TestSetID,
)


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


class RunScores(BaseModel):
    scores: dict[str, float] | None = Field(
        ...,
        description=(
            'Per-metric scores produced when the run completed, keyed by metric '
            'name (e.g. `{"exact_match": 1.0, "bleu": 0.42}`). Populated only once '
            'the run reaches `Completed`; mutually exclusive with `error` — a run '
            'either scores successfully or fails, never both.'
        ),
    )


class RunError(BaseModel):
    error: str | None = Field(
        ...,
        description=(
            'Error message describing why the run failed. Populated instead of '
            '`scores` when the run reaches `Failed`; mutually exclusive with '
            '`scores`.'
        ),
    )


class RunExecutionDate(BaseModel):
    executed_at: datetime | None = Field(
        ...,
        description=(
            'Timestamp when the run finished executing, successfully or not — '
            'distinct from `created_at`, which marks when the run was enqueued '
            'as `Pending`. Set once the run reaches a terminal status '
            '(`Completed` or `Failed`).'
        ),
    )


class StandaloneRunCreationMetadata(RunID, RunStatus, RunCreationDate):
    test_case_id: TestCaseID = Field(
        ...,
        description='ID of the live test this standalone run was created for.',
    )


class PaginatedStandaloneRunCreationMetadata(Pagination):
    items: list[StandaloneRunCreationMetadata]


class StandaloneRunDetails(StandaloneRunCreationMetadata, RunScores, RunError, RunExecutionDate):
    pass


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


class TestSetExecutionMetadata(TestSetExecutionID, TestSetExecutionCreationDate):
    test_set_id: TestSetID
    run_count: int = Field(
        ...,
        description=(
            'Number of TestRunModel rows this execution produced — one per '
            'entry it targeted, fanned out fresh from the test set\'s live '
            'entries if `replayed_execution_id` is null, or re-pointed at '
            'the entries `replayed_execution_id` originally used otherwise.'
        ),
    )
    replayed_execution_id: TestSetReplayedExecutionID | None = Field(
        ...,
        description=(
            'Null if this was a live execution — its runs were fanned out '
            'fresh from the test set\'s entries at the moment it was '
            'triggered. Set if this was a replay — the ID of the prior '
            'execution whose exact entries this one re-targeted.'
        ),
    )


class PaginatedTestSetExecutionMetadata(Pagination):
    items: list[TestSetExecutionMetadata]


class TestSetExecutionRunMetadata(RunID, RunStatus, RunCreationDate):
    test_set_entry_id: TestSetEntryID
    test_set_execution_id: TestSetExecutionID


class PaginatedTestSetExecutionRunMetadata(Pagination):
    items: list[TestSetExecutionRunMetadata]


class TestSetExecutionRunDetails(TestSetExecutionRunMetadata, RunScores, RunError,
                                 RunExecutionDate, CreateTestCaseRequest):
    test_case_id: TestCaseID
    test_set_id: TestSetID
    test_case_snapshot_at: TestCaseSnapshotDate


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


class TestPlanExecutionMetadata(TestPlanExecutionID, TestPlanExecutionCreationDate):
    test_plan_id: TestPlanID
    run_count: int = Field(
        ...,
        description=(
            'Number of TestRunModel rows this execution produced — one per '
            'entry it targeted, fanned out fresh from every test set '
            'currently linked to the plan if `replayed_execution_id` is '
            'null, or re-pointed at the entries `replayed_execution_id` '
            'originally used otherwise.'
        ),
    )
    replayed_execution_id: TestPlanReplayedExecutionID | None = Field(
        ...,
        description=(
            'Null if this was a live execution — its runs were fanned out '
            "fresh from the plan's linked test sets at the moment it was "
            'triggered. Set if this was a replay — the ID of the prior '
            'execution whose exact entries this one re-targeted.'
        ),
    )


class PaginatedTestPlanExecutionMetadata(Pagination):
    items: list[TestPlanExecutionMetadata]


class TestPlanExecutionRunMetadata(RunID, RunStatus, RunCreationDate):
    test_set_entry_id: TestSetEntryID
    test_plan_execution_id: TestPlanExecutionID


class PaginatedTestPlanExecutionRunMetadata(Pagination):
    items: list[TestPlanExecutionRunMetadata]


class TestPlanExecutionRunDetails(TestPlanExecutionRunMetadata, RunScores, RunError,
                                 RunExecutionDate, CreateTestCaseRequest):
    test_case_id: TestCaseID
    test_set_id: TestSetID | None
    test_plan_id: TestPlanID


class RunOrigin(StrEnum):
    standalone = "Standalone"
    test_set = "TestSet"
    test_plan = "TestPlan"


class RunMetadata(RunID, RunStatus, RunCreationDate):
    origin: RunOrigin = Field(
        ...,
        description=(
            'Which of the three ways this run was created: `Standalone` '
            '(created directly against a live test via '
            '`POST /runs/standalone/{test_id}`), `TestSet` (created as part '
            'of a test set execution), or `TestPlan` (created as part of a '
            'test plan execution).'
        ),
    )
    test_case_id: TestCaseID | None = Field(
        ...,
        description=(
            'ID of the live test this run targets. Set only if `origin` is '
            '`Standalone`; null otherwise.'
        ),
    )
    test_set_entry_id: TestSetEntryID | None = Field(
        ...,
        description=(
            'ID of the frozen test set entry this run targets. Set if '
            '`origin` is `TestSet` or `TestPlan`; null if `origin` is '
            '`Standalone`.'
        ),
    )
    test_set_execution_id: TestSetExecutionID | None = Field(
        ...,
        description=(
            'ID of the test set execution that produced this run. Set only '
            'if `origin` is `TestSet`; null otherwise.'
        ),
    )
    test_plan_execution_id: TestPlanExecutionID | None = Field(
        ...,
        description=(
            'ID of the test plan execution that produced this run. Set only '
            'if `origin` is `TestPlan`; null otherwise.'
        ),
    )


class PaginatedRunMetadata(Pagination):
    items: list[RunMetadata]


class ExecutionOrigin(StrEnum):
    test_set = "TestSet"
    test_plan = "TestPlan"


class ExecutionMetadata(BaseModel):
    id: uuid.UUID = Field(
        ...,
        description=(
            'Unique identifier of the execution (`TestSetExecutionModel.id` '
            'or `TestPlanExecutionModel.id`, depending on `origin`).'
        ),
    )
    created_at: datetime = Field(
        ...,
        description=(
            'Timestamp when the execution was triggered (when its runs got '
            'enqueued as `Pending`), not when they started or finished '
            'executing.'
        ),
    )
    origin: ExecutionOrigin = Field(
        ...,
        description=(
            'Which of the two ways this execution was created: `TestSet` '
            '(from `POST /runs/test-sets/{test_set_id}` or its replay) or '
            '`TestPlan` (from `POST /runs/test-plans/{test_plan_id}` or its '
            'replay).'
        ),
    )
    run_count: int = Field(
        ...,
        description='Number of TestRunModel rows this execution produced.',
    )
    test_set_id: TestSetID | None = Field(
        ...,
        description=(
            'ID of the test set this execution belongs to. Set only if '
            '`origin` is `TestSet`; null otherwise.'
        ),
    )
    test_plan_id: TestPlanID | None = Field(
        ...,
        description=(
            'ID of the test plan this execution belongs to. Set only if '
            '`origin` is `TestPlan`; null otherwise.'
        ),
    )
    replayed_test_set_execution_id: TestSetReplayedExecutionID | None = Field(
        ...,
        description=(
            'ID of the prior test set execution this one replayed. Set '
            'only if `origin` is `TestSet` and this execution was itself a '
            'replay; null for a live test set execution or any test plan '
            'execution.'
        ),
    )
    replayed_test_plan_execution_id: TestPlanReplayedExecutionID | None = Field(
        ...,
        description=(
            'ID of the prior test plan execution this one replayed. Set '
            'only if `origin` is `TestPlan` and this execution was itself a '
            'replay; null for a live test plan execution or any test set '
            'execution.'
        ),
    )


class PaginatedExecutionMetadata(Pagination):
    items: list[ExecutionMetadata]
