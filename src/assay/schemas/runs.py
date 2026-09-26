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
            'Current status of the run — lifecycle up to a point, then the '
            'outcome itself. `Pending` (created but not yet executed) and '
            '`Running` are the only non-terminal values; a newly created run '
            'is always `Pending`. The terminal values are a roll-up of every '
            'assigned test type\'s own pass/fail (see `results`): `Green` '
            '(every assigned type passed), `Amber` (some passed, some '
            'didn\'t), `Red` (every assigned type was evaluated and none '
            'passed), or `NotRan` (nothing could be attempted at all — the '
            'model couldn\'t be called, the entry couldn\'t be read — the '
            'only status `error` is ever set for).'
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


class TestTypeResult(BaseModel):
    passed: bool = Field(
        ...,
        description=(
            'Whether this test type\'s own pass criterion was met — exact/regex/'
            'substring match for `deterministic` types, score vs. the type\'s own '
            '`threshold` config field for `nlp_metric` types, or the judge\'s own '
            'verdict for `llm_as_judge` types. Always present; every test type '
            'must resolve to a boolean, regardless of whether it also produces '
            'a `score`.'
        ),
    )
    score: float | None = Field(
        ...,
        description=(
            'The raw numeric result, when this test type produces one — always '
            'for `deterministic` and `nlp_metric` types, sometimes for '
            '`llm_as_judge` types. Null when the type has no natural score '
            '(e.g. a judge verdict with nothing to reduce to a number) or when '
            'this type failed to evaluate at all (see `detail`).'
        ),
    )
    detail: str | None = Field(
        ...,
        description=(
            'Free text alongside the result — an `llm_as_judge` type\'s '
            'rationale for its verdict, or this specific type\'s own error '
            'message if it individually failed to evaluate (e.g. a judge API '
            'timeout) while the rest of the run\'s other assigned types '
            'proceeded normally. Null when there\'s nothing to add.'
        ),
    )


class RunResults(BaseModel):
    results: dict[str, TestTypeResult] | None = Field(
        ...,
        description=(
            'Per-test-type results, keyed by assigned test type name (e.g. '
            '`{"ROUGE": {"passed": true, "score": 0.81, "detail": null}, '
            '"Toxicity": {"passed": false, "score": null, "detail": "..."}}`. '
            'Populated once the run reaches `Green`, `Amber`, or `Red` — one '
            'entry per test type that was assigned to the test/entry this run '
            'targeted. Null while `Pending`/`Running`, and for `NotRan` (see '
            '`error` instead).'
        ),
    )


class RunError(BaseModel):
    error: str | None = Field(
        ...,
        description=(
            'Error message describing why the run could not be executed at '
            'all — the model couldn\'t be called, the entry couldn\'t be read. '
            'Populated only when the run reaches `NotRan`; mutually exclusive '
            'with `results`, which stays null in that case. Never set for an '
            'individual test type failing its own pass criterion — that shows '
            'up as that type\'s own `results[name].detail` instead, alongside '
            'whatever other types did produce a result.'
        ),
    )


class RunExecutionDate(BaseModel):
    executed_at: datetime | None = Field(
        ...,
        description=(
            'Timestamp when the run finished executing, successfully or not — '
            'distinct from `created_at`, which marks when the run was enqueued '
            'as `Pending`. Set once the run reaches a terminal status '
            '(`Green`, `Amber`, `Red`, or `NotRan`).'
        ),
    )


class StandaloneRunCreationMetadata(RunID, RunStatus, RunCreationDate):
    test_case_id: TestCaseID = Field(
        ...,
        description='ID of the live test this standalone run was created for.',
    )


class PaginatedStandaloneRunCreationMetadata(Pagination):
    items: list[StandaloneRunCreationMetadata]


class StandaloneRunDetails(StandaloneRunCreationMetadata, RunResults, RunError,
                           RunExecutionDate, CreateTestCaseRequest):
    test_case_snapshot_at: TestCaseSnapshotDate


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


class TestSetExecutionRunDetails(TestSetExecutionRunMetadata, RunResults, RunError,
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


class TestPlanExecutionRunDetails(TestPlanExecutionRunMetadata, RunResults, RunError,
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
    test_set_id: TestSetID | None = Field(
        ...,
        description=(
            'ID of the test set the producing execution belongs to. Set only '
            'if `origin` is `TestSet`; null otherwise. Lets a caller deep-link '
            'to the test set without a separate lookup from '
            '`test_set_execution_id`.'
        ),
    )
    test_plan_id: TestPlanID | None = Field(
        ...,
        description=(
            'ID of the test plan the producing execution belongs to. Set '
            'only if `origin` is `TestPlan`; null otherwise. Lets a caller '
            'deep-link to the test plan without a separate lookup from '
            '`test_plan_execution_id`.'
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
