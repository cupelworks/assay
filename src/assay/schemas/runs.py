import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from assay.models import Comparison, OutputSource, TestStatus
from assay.schemas import (
    CreateTestCaseRequest,
    Pagination,
    TestCaseID,
    TestCaseSnapshotDate,
    TestPlanID,
    TestSetEntryID,
    TestSetID,
)
from assay.schemas.settings import JudgeProvider, JudgeSettings


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


class EvaluationInput(BaseModel):
    """Everything one engine call needs, and nothing else.

    Built by the evaluator registry from the run's frozen copy of the test,
    the assignment and the type's catalogue row, so an engine is a pure
    function of this object: it never sees an ORM model, never queries, and
    can't tell a standalone run's copy from a test set entry — the two
    frozen shapes are already collapsed into these fields.
    """
    model_config = ConfigDict(frozen=True)

    input: str = Field(description='The prompt the application was (or will be) asked.')
    reference: str | None = Field(
        description=(
            'The expected answer — the entry\'s `expected_output`, where a '
            '`reference`-kind config field resolves from. Null when the test '
            'has none.'
        ),
    )
    answer: str = Field(
        description=(
            'The text being scored — the copy\'s recorded `model_output`, or the '
            'application\'s reply obtained during the run when the test had '
            'none. Always present: a run that couldn\'t obtain an answer is '
            '`NotRan` and never reaches an engine.'
        ),
    )
    config: dict[str, str] = Field(
        default_factory=dict,
        description=(
            'The assignment\'s own config values (`threshold`, `pattern`, '
            '`rubric`, ...), keyed by the type\'s `config_fields[].key`. Empty '
            'when the type takes nothing but the reference.'
        ),
    )
    engine_settings: dict = Field(
        default_factory=dict,
        description=(
            'The catalogue row\'s `engine_settings` — the engine\'s parameters for this type.'
        ),
    )
    comparison: Comparison | None = Field(
        default=None,
        description=(
            'The catalogue row\'s `comparison` — how a threshold-scored engine turns '
            'its score into `passed`. Null for a type not scored against a threshold.'
        ),
    )
    judge: JudgeSettings | None = Field(
        default=None,
        description=(
            'The judge settings in effect for this run, for the LLM-judge engine — read '
            'once per run that has a judge check. Null for every other engine.'
        ),
    )


class RubricSource(StrEnum):
    custom = "custom"
    default = "default"


class JudgeRubric(BaseModel):
    """The rubric an LLM judge graded with."""
    text: str = Field(description="The rubric, exactly as the judge received it.")
    source: RubricSource = Field(
        description="`custom`: the assignment's own `rubric`. `default`: the type's "
                    "`default_rubric`, because the assignment set none (or only "
                    "whitespace).",
    )


class JudgeIdentity(BaseModel):
    """Which model gave an LLM judge's verdict."""
    provider: JudgeProvider = Field(
        description="The API the judge was called through: `anthropic` or `openai` "
                    "(which includes any OpenAI-compatible server).",
    )
    model: str = Field(description="The model name the judge was called with.")


class TestTypeResult(BaseModel):
    passed: bool = Field(
        ...,
        description=(
            'Whether this test type\'s own pass criterion was met — the check\'s '
            'own rule for a deterministic engine (a match, a substring, a '
            'pattern, valid JSON, a length limit), `score` against the assignment\'s '
            '`threshold` (in the direction of the type\'s `comparison`) for a '
            'metric engine, or the judge\'s own verdict for an LLM-judge engine. '
            'Always present; every test type must resolve to a boolean, '
            'regardless of whether it also produces a `score`.'
        ),
    )
    score: float | None = Field(
        ...,
        description=(
            'The raw numeric result, on the type\'s own native scale (see the '
            'type\'s `threshold` bounds in `GET /tests/types`) — set exactly '
            'when the type measures something on a scale: always for a metric '
            'type (ROUGE, BLEU, …), sometimes for an LLM judge that returns a '
            'rating. Only comparable within one type. Null for every '
            'deterministic type (Exact Match, Contains, Regex Match, the JSON '
            'checks, the length limits and their variants — pass/fail by nature, '
            '`passed` is the whole result), for a judge verdict with no '
            'numeric rating, and when this type failed to evaluate at all (see '
            '`detail`).'
        ),
    )
    detail: str | None = Field(
        ...,
        description=(
            'Free text alongside the result — an LLM judge\'s rationale for its '
            'verdict, or this specific type\'s own error message if it '
            'individually failed to evaluate (e.g. a judge API timeout, a bad '
            '`threshold` value, an engine this worker doesn\'t have) while the '
            'rest of the run\'s other assigned types proceeded normally. Null '
            'when there\'s nothing to add.'
        ),
    )
    engine: str | None = Field(
        default=None,
        description=(
            'Which evaluator engine produced this result, as the type\'s '
            'catalogue row named it at execution time. Recorded on the run so a '
            'later catalogue change can\'t silently reinterpret an old result. '
            'Null only when the type was not in the catalogue at all when the '
            'run executed (then `detail` says so), and on results written '
            'before this field existed.'
        ),
    )
    engine_settings: dict | None = Field(
        default=None,
        description=(
            'The engine\'s settings for this type, exactly as the catalogue row '
            'held them at execution time (same shape as `GET /tests/types`\' '
            '`engine_settings`). Null in the same two cases as `engine`.'
        ),
    )
    answer_path: str | None = Field(
        default=None,
        description=(
            'The part of the answer this check read, when its assignment set one '
            '(a JSONPath into `application_reply`, or into a recorded answer that '
            'is JSON). Null: it read `evaluated_output`, the answer at the '
            'settings\' output path, like every check without its own path.'
        ),
    )
    rubric: JudgeRubric | None = Field(
        default=None,
        description=(
            'For an LLM-judge type: the rubric the judge graded with, and whether '
            'it was the assignment\'s own or the type\'s default — recorded at '
            'execution time, so it stays right if the test or the catalogue '
            'changes later. Null for every other type, for a judge that couldn\'t '
            'be asked (then `detail` says why), and on results written before '
            'this field existed.'
        ),
    )
    test_type: str | None = Field(
        default=None,
        description=(
            'The catalogue type this check ran, e.g. `Contains`. A run\'s `results` '
            'are keyed by each assignment\'s label, which is the type\'s name '
            'unless it was renamed or the type is assigned more than once '
            '(`Contains 2`), so this is what says which type a result is. Null '
            'only on results written before this field existed.'
        ),
    )
    errored: bool = Field(
        default=False,
        description=(
            'True when this check couldn\'t be evaluated at all — it raised an error '
            '(no judge configured, the judge answered HTTP 503, an invalid pattern, a '
            'type no longer in the catalogue) — rather than evaluating the answer and '
            'failing. `passed` is false either way and `detail` says why; this is what '
            'tells the two apart. Statistics count an errored check apart: it says '
            'nothing about the application.'
        ),
    )
    judge: JudgeIdentity | None = Field(
        default=None,
        description=(
            'For an LLM-judge type: the provider and model that gave the verdict, '
            'recorded at execution time — the judge settings can change at any '
            'moment, so this is what says which model a verdict came from. Null '
            'for every other type, for a judge that couldn\'t be asked (then '
            '`detail` says why), and on results written before this field existed.'
        ),
    )


class RunResults(BaseModel):
    results: dict[str, TestTypeResult] | None = Field(
        ...,
        description=(
            'Per-check results, keyed by each assignment\'s label — the test '
            'type\'s name unless renamed, numbered when a type is assigned more '
            'than once (`Contains`, `Contains 2`); each result\'s `test_type` '
            'says which type it is (e.g. '
            '`{"ROUGE": {"passed": true, "score": 0.81, "detail": null, '
            '"engine": "rouge", "engine_settings": {"variant": "rougeL", ...}}, '
            '"Toxicity": {"passed": false, "score": null, "detail": "...", '
            '"engine": "llm_judge", "engine_settings": {...}}}`. Each entry also '
            'records the engine and settings it was scored with, so the result '
            'stays interpretable if the catalogue changes later. Populated once '
            'the run reaches `Green`, `Amber`, or `Red` — one entry per check '
            'assigned to the test/entry this run targeted. Null '
            'while `Pending`/`Running`, and for `NotRan` (see `error` instead).'
        ),
    )


class RunError(BaseModel):
    error: str | None = Field(
        ...,
        description=(
            'Error message describing why the run could not be executed at '
            'all — the application under test couldn\'t be reached, failed, '
            'or had nothing at the output path; the saved settings are '
            'invalid; the entry couldn\'t be read. An application that answers '
            'with nothing is not an error: that empty answer is scored. '
            'Populated only when the run reaches `NotRan`; mutually exclusive '
            'with `results`, which stays null in that case. Never set for an '
            'individual test type failing its own pass criterion — that shows '
            'up as that type\'s own `results[name].detail` instead, alongside '
            'whatever other types did produce a result.'
        ),
    )


class RunEvaluatedOutput(BaseModel):
    evaluated_output: str | None = Field(
        ...,
        description=(
            'The answer this run scored — what every check in `results` reads, '
            'unless its own `answer_path` pointed it at another part of '
            '`application_reply`. Set once the run reaches `Green`, `Amber` or '
            '`Red`, whether the answer was the test\'s recorded `model_output` '
            '(copied here, so the results always sit next to the exact text they '
            'judged) or was obtained from the application under test during the '
            'run: the value at the settings\' output path, as JSON text when it\'s '
            'structured, `""` when the application answered with nothing. '
            'Null while `Pending`/`Running`, for `NotRan`, and on runs executed '
            'before this field existed.'
        ),
    )
    output_source: OutputSource | None = Field(
        ...,
        description=(
            'Where `evaluated_output` came from: `recorded` — the test already had '
            'a `model_output` and the run scored that; `application` — the test '
            'had none, so the run called the application under test (with the '
            'settings saved from the UI, else `ASSAY_TARGET_*`) and scored its '
            'reply, which is why two '
            'runs of the same test can legitimately differ. Null whenever '
            '`evaluated_output` is.'
        ),
    )
    application_reply: Any = Field(
        None,
        description=(
            'The application\'s whole reply, parsed, when `output_source` is '
            '`application`: the model\'s output together with the application\'s '
            'own fields (e.g. `stop_reason`, token counts, `model`). A check with '
            'its own `answer_path` read its part of this. Null for a recorded '
            'answer, for `NotRan`, and on runs executed before it was kept.'
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


class BatchMembership(BaseModel):
    """Whether a run or execution belongs to a statistical batch
    (`POST /statistics/batches`)."""
    batch_id: uuid.UUID | None = Field(
        default=None,
        description=(
            'The statistical batch this was created by, or null for an ordinary run or '
            'execution (run once, or replayed). A batch runs its scope N times, so its '
            'runs and executions are listed here with everything else; filter them out '
            'with `?batch=none`, or keep one batch\'s with `?batch=<batch_id>`. Open the '
            'batch at `GET /statistics/batches/{batch_id}`.'
        ),
    )
    batch_index: int | None = Field(
        default=None,
        description=(
            'Which time of its batch this belongs to, 1 to the batch\'s '
            '`times_requested`; null outside a batch.'
        ),
    )


class StandaloneRunCreationMetadata(BatchMembership, RunID, RunStatus, RunCreationDate):
    test_case_id: TestCaseID = Field(
        ...,
        description='ID of the live test this standalone run was created for.',
    )


class PaginatedStandaloneRunCreationMetadata(Pagination):
    items: list[StandaloneRunCreationMetadata]


class StandaloneRunDetails(StandaloneRunCreationMetadata, RunResults, RunError,
                           RunEvaluatedOutput, RunExecutionDate, CreateTestCaseRequest):
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


class TestSetExecutionMetadata(BatchMembership, TestSetExecutionID,
                               TestSetExecutionCreationDate):
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
                                 RunEvaluatedOutput, RunExecutionDate, CreateTestCaseRequest):
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


class TestPlanExecutionMetadata(BatchMembership, TestPlanExecutionID,
                                TestPlanExecutionCreationDate):
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
                                  RunEvaluatedOutput, RunExecutionDate, CreateTestCaseRequest):
    test_case_id: TestCaseID
    test_set_id: TestSetID | None
    test_plan_id: TestPlanID


class RunOrigin(StrEnum):
    standalone = "Standalone"
    test_set = "TestSet"
    test_plan = "TestPlan"


class RunMetadata(BatchMembership, RunID, RunStatus, RunCreationDate):
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


class ExecutionMetadata(BatchMembership):
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
