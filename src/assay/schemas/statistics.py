"""Run with statistics: the catalogue of statistical tests, the estimate shown
before a batch is created, batches and their results, and comparisons of two
batches (docs/statistics/).

Conventions every response here follows, so a chart never has to guess
(docs/statistics/dev_notes.md note 15): numbers are JSON numbers rounded to
four decimals, never strings; `null` where a value doesn't exist (no score on
a pass/fail check, no result for a run that wasn't evaluated), never a
sentinel; every series is in the same order with the same `index`; every
interval says what it is (`method`, `level`, `sides`).
"""
import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from assay.schemas._common import Pagination

# ── shared vocabulary ────────────────────────────────────────────────────────


class StatisticalEngine(StrEnum):
    """The arithmetic in code a statistical test runs. A `statistical_tests`
    row names one, and several tests can share an engine with different
    parameters; `kind` in the catalogue says which engines run as a batch and
    which compare two batches."""
    binomial_gate = "binomial_gate"
    one_sample_t = "one_sample_t"
    judge_stability = "judge_stability"
    pass_rates = "pass_rates"
    no_worse = "no_worse"
    mean_scores = "mean_scores"
    score_ranks = "score_ranks"
    paired_entries = "paired_entries"


class StatisticalTestKind(StrEnum):
    batch = "batch"
    comparison = "comparison"


class ScopeKind(StrEnum):
    test = "test"
    test_set = "test_set"
    test_plan = "test_plan"


class Reads(StrEnum):
    pass_fail = "pass_fail"
    scores = "scores"


class AppliesTo(StrEnum):
    every_check = "every_check"
    scored_checks = "scored_checks"
    judge_checks = "judge_checks"


class FloorKind(StrEnum):
    exact = "exact"
    rule_of_thumb = "rule_of_thumb"
    none = "none"


class IntervalMethod(StrEnum):
    exact = "exact"
    wilson = "wilson"
    t = "t"
    newcombe = "newcombe"


class IntervalSides(StrEnum):
    one = "one"
    two = "two"


class Interval(BaseModel):
    """A range with its point estimate, and what kind of range it is."""
    lower: float | None = Field(
        description="The lower bound; null when it can't be computed (one score has no "
                    "spread).",
    )
    point: float | None = Field(description="The estimate itself: a rate, a mean, a difference.")
    upper: float | None = Field(description="The upper bound; null when it can't be computed.")
    method: IntervalMethod = Field(
        description="How it was computed. `exact`: Clopper–Pearson, from the binomial "
                    "distribution itself. `wilson`: the Wilson score interval, the usual "
                    "\"give or take\" of a rate. `t`: Student's t, for a mean score. "
                    "`newcombe`: Newcombe's score interval of a difference of two rates.",
    )
    level: float = Field(description="The confidence level, e.g. 0.95.")
    sides: IntervalSides = Field(
        description="`one`: each bound is a one-sided bound at `level` — the kind a "
                    "verdict \"at least X\" or \"at most X\" is drawn from, so 95% one-sided "
                    "bounds together span 90%. `two`: the range as a whole covers the true "
                    "value with probability `level`.",
    )


class ScopeRequest(BaseModel):
    """What to run: exactly one of a test, a test set or a test plan."""
    model_config = ConfigDict(extra="forbid")

    test_id: uuid.UUID | None = Field(
        None, description="A standalone test: each time is one standalone run of it.")
    test_set_id: uuid.UUID | None = Field(
        None, description="A test set: each time is one live execution of the set — a run "
                          "per entry.")
    test_plan_id: uuid.UUID | None = Field(
        None, description="A test plan: each time is one live execution of the plan — a "
                          "run per entry of every linked set.")

    @model_validator(mode="after")
    def _exactly_one_scope(self) -> "ScopeRequest":
        given = [v for v in (self.test_id, self.test_set_id, self.test_plan_id) if v]
        if len(given) != 1:
            raise ValueError("Give exactly one of test_id, test_set_id or test_plan_id")
        return self


class Scope(BaseModel):
    kind: ScopeKind = Field(description="What the batch runs: a test, a test set or a plan.")
    id: uuid.UUID = Field(description="The test's, set's or plan's id.")
    name: str = Field(description="Its name, as it is now.")


class Warning_(BaseModel):
    """Something the user should know before paying for the runs."""
    code: str = Field(
        description="Stable identifier for the FE: `recorded_answers` (some entries have a "
                    "recorded answer, so only their judge checks can vary), "
                    "`nothing_can_vary` (an entry has a recorded answer and no judge "
                    "check: every run of it gives the same result), `no_judge_configured` "
                    "(judge checks exist but no judge model is chosen: they'll fail as "
                    "errors and say nothing), `no_application_configured` (entries need the "
                    "application's answer but no URL is set: their runs will be Not Ran), "
                    "`judge_settings_invalid` / `target_settings_invalid` (the saved "
                    "settings no longer validate), `checks_not_applicable` (some checks "
                    "aren't covered by this statistical test, e.g. pass/fail checks under "
                    "a t-test), `times_capped` (`times` was left out and the suggested size "
                    "would create more runs than a batch may: the estimate is for the most "
                    "this scope can run instead).",
    )
    message: str = Field(description="One sentence to show as it is.")


# ── the catalogue ────────────────────────────────────────────────────────────


class ParameterKind(StrEnum):
    rate = "rate"
    level = "level"
    share = "share"


class ParameterDescriptor(BaseModel):
    key: str = Field(description="The key to send under `parameters`.")
    label: str = Field(description="A label for the input.")
    kind: ParameterKind = Field(
        description="`rate`: a pass rate, 0–1 (0.9 = 90%). `level`: a confidence level, "
                    "0–1. `share`: a share of a check's score range, 0–1 (0.05 of a 0–100 "
                    "BLEU range is 5 points).",
    )
    default: float = Field(description="Used when the key is left out.")
    min: float = Field(description="The smallest accepted value, inclusive.")
    max: float = Field(description="The largest accepted value, inclusive.")
    hint: str = Field(description="One line on what the value means.")


class FloorDescriptor(BaseModel):
    kind: FloorKind = Field(
        description="Where the minimum number of times comes from. `exact`: arithmetic — "
                    "below it no result could ever pass (the binomial gate). "
                    "`rule_of_thumb`: the maths is only approximately right below it (the "
                    "t-test). `none`: the test works at any size (Fisher's exact test).",
    )
    formula: str | None = Field(description="The formula, when there is one.")
    explanation: str = Field(description="Where the number comes from, in plain words.")
    examples: list[dict] = Field(
        description="Worked values, e.g. `{\"target\": 0.9, \"confidence\": 0.95, "
                    "\"times\": 29}`.",
    )


class StatisticalTestDescriptor(BaseModel):
    """One entry of the catalogue: everything the "run with statistics" dialog
    needs to offer a test and explain it."""
    id: str = Field(description="What to send as `statistical_test`: the catalogue row's id.")
    name: str = Field(description="Its name, e.g. \"Binomial gate\".")
    engine: StatisticalEngine = Field(
        description="The arithmetic in code this test runs. The catalogue is a table, like "
                    "the check types: a row names its engine and carries the texts, each "
                    "parameter's default and range and the engine's settings, so several "
                    "tests can share one engine with different parameters.",
    )
    kind: StatisticalTestKind = Field(
        description="`batch`: run as a batch of N times (POST /statistics/batches). "
                    "`comparison`: reads two finished batches (POST /statistics/comparisons).",
    )
    wave: int = Field(description="The design's build wave (1 or 2). Informational.")
    question: str = Field(description="The question it answers, in plain words.")
    reads: Reads = Field(
        description="`pass_fail`: each check's pass or fail per run — every check type has "
                    "one. `scores`: each check's score per run — only checks scored on a "
                    "scale (ROUGE, BLEU, METEOR, BERTScore, Cosine Similarity).",
    )
    applies_to: AppliesTo = Field(
        description="Which checks get a verdict: `every_check`, `scored_checks` (the "
                    "threshold-scored ones), `judge_checks`. Other checks still show their "
                    "pass rate, without a verdict.",
    )
    verdicts: list[str] = Field(
        description="The verdicts it can give: `pass`/`fail`/`inconclusive` for a batch "
                    "test; `better`/`worse`/`no_difference` for a comparison, except "
                    "`no_worse`/`worse`/`inconclusive` for the no-worse test.",
    )
    parameters: list[ParameterDescriptor] = Field(
        description="What can be set, each with its default and range. Send them under "
                    "`parameters`; any left out take the default.",
    )
    engine_settings: dict = Field(
        description="The engine's own constants for this test, as its catalogue row sets "
                    "them — the t-test's `floor` and `recommended_times`; empty for most.",
    )
    floor: FloorDescriptor = Field(
        description="The minimum number of times, and where it comes from.",
    )
    recommended_times: int | None = Field(
        description="A size the test is comfortable at, when the floor is only a rule of "
                    "thumb (30 for the t-test); null otherwise.",
    )
    method: str = Field(description="The arithmetic behind the verdict, named.")


class StatisticalTestCatalogue(BaseModel):
    items: list[StatisticalTestDescriptor]
    max_times: int = Field(description="The most times one batch can run.")
    max_runs: int = Field(
        description="The most runs one batch can create: times × entries per time.",
    )


# ── the estimate ─────────────────────────────────────────────────────────────


class CheckRef(BaseModel):
    """One check of the scope: its entry (null for a standalone test) and its
    label."""
    model_config = ConfigDict(extra="forbid")

    entry_id: uuid.UUID | None = Field(
        default=None, description="The test set entry; null for a standalone test.")
    label: str = Field(description="The check's label within its test.")


class CheckTarget(CheckRef):
    """A check's own target, instead of the batch's."""
    target: float = Field(description="The check's target, within the parameter's range.")


class EstimateRequest(ScopeRequest):
    """POST /statistics/estimate: what a batch would need and cost. Nothing is
    created."""
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [
            {"test_set_id": "4e86003a-9e28-4c93-a08e-f99c6acbaab6",
             "statistical_test": "binomial_gate",
             "parameters": {"target": 0.9, "confidence": 0.95}},
            {"test_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
             "statistical_test": "one_sample_t", "parameters": {"difference": 0.05},
             "times": 30},
        ]},
    )

    statistical_test: str = Field(
        description="The `id` of a batch test from `GET /statistics/tests` (`kind: batch`); "
                    "an unknown id is a 422.",
    )
    parameters: dict[str, float] = Field(
        default_factory=dict,
        description="The test's parameters by key (see the catalogue); any left out take "
                    "their default.",
    )
    times: int | None = Field(
        None, ge=1,
        description="How many times to run the scope. Left out: the suggestion marked "
                    "`default`.",
    )
    targets: list[CheckTarget] = Field(
        default_factory=list,
        description="Checks with their own target instead of the batch's (\"Relevance at 8 "
                    "in 10\"), for a test with a target. Each must name a check of the scope, "
                    "once.",
    )
    leave_out: list[CheckRef] = Field(
        default_factory=list,
        description="Checks to leave out of the batch: its runs skip them (their judge "
                    "calls aren't made) and they get no verdict. Each entry keeps at least "
                    "one check.",
    )


class SuggestionKind(StrEnum):
    floor = "floor"
    absorbs_not_ran = "absorbs_not_ran"
    allows_one_miss = "allows_one_miss"
    detects_difference = "detects_difference"
    recommended = "recommended"
    reaches_goal = "reaches_goal"
    worth_its_cost = "worth_its_cost"
    best_chance = "best_chance"
    trial = "trial"


class OutcomeSplit(BaseModel):
    """How a batch of this size would most likely end, from the history:
    chances that add up to 1."""
    passed: float = Field(description="Every check passes.")
    inconclusive: float = Field(description="No check fails and at least one can't be told.")
    failed: float = Field(description="At least one check fails.")


class Suggestion(BaseModel):
    times: int = Field(description="A number of times worth offering.")
    kind: SuggestionKind = Field(
        description="Why: `floor` (the least that can conclude), `absorbs_not_ran` (one "
                    "more, so a run that can't be evaluated doesn't cost the verdict), "
                    "`allows_one_miss` (the gate still passes with one failure), "
                    "`detects_difference` (enough to see the difference you set, 80% of the "
                    "time), `recommended` (where the test's maths is comfortable). With a "
                    "history for every check: `reaches_goal` (the fewest times with a "
                    "`goal` chance that every check gets an answer), and when no size "
                    "reaches it `worth_its_cost` (the fewest times within 5 points of the "
                    "best chance) and `best_chance` (the size with the best chance). `trial`: "
                    "the trial's size.",
    )
    label: str = Field(description="A short caption, e.g. \"29 times · no failure allowed\".")
    default: bool = Field(description="The one used when `times` is left out.")
    chance: float | None = Field(
        default=None,
        description="The chance that every check gets an answer at this size, from the "
                    "checks' history (averaged over what their rates could be). Null when a "
                    "check has no history.",
    )
    failures_allowed: int | None = Field(
        default=None,
        description="For a target test: failures a check may have at this size and still "
                    "pass, at the batch's target. Null otherwise.",
    )
    outcome: OutcomeSplit | None = Field(
        default=None, description="How the batch would most likely end; null without "
                                  "history.")
    likely_undecided: CheckRef | None = Field(
        default=None,
        description="The check most likely to stay undecided at this size; null when none "
                    "has a real chance of it, or without history.",
    )


class GateRuleSchema(BaseModel):
    """What a binomial gate at this many times decides, before running."""
    times: int
    pass_at_least: int | None = Field(
        description="Passes needed (per check) to pass the gate; null when no count can "
                    "pass at this size.",
    )
    fail_at_most: int | None = Field(
        description="Passes at or below which the gate fails; null when no count can fail "
                    "at this size.",
    )


class CallCount(BaseModel):
    per_time: int = Field(description="Calls one time makes.")
    total: int = Field(description="Calls the whole batch makes: per_time × times.")


class Calls(BaseModel):
    """What the batch will pay for. Retries aren't counted."""
    application: CallCount = Field(
        description="Calls to the application under test: one per run of an entry with no "
                    "recorded answer.",
    )
    judge: CallCount = Field(
        description="Calls to the judge model: one per LLM-judge check per run.",
    )


class Outlook(StrEnum):
    likely_pass = "likely_pass"
    likely_fail = "likely_fail"
    too_close = "too_close"
    unknown = "unknown"
    certain = "certain"


class CheckHistoryOut(BaseModel):
    """What earlier runs showed about the check: its most recent runs (at
    most 200) that asked the same question and gave it a result."""
    runs: int = Field(description="Runs that gave the check a result.")
    passed: int = Field(description="Of those, the ones it passed.")
    rate: float = Field(description="passed / runs.")
    mean: float | None = Field(
        default=None, description="For a scored check: the scores' average.")
    sd: float | None = Field(
        default=None, description="For a scored check: how much the scores varied.")


class CheaperKind(StrEnum):
    lower_target = "lower_target"
    leave_out = "leave_out"
    less_sure = "less_sure"


class CheaperOption(BaseModel):
    """A change that makes an answer more likely or the batch cheaper, with
    what it does to the batch."""
    kind: CheaperKind = Field(
        description="`lower_target`: a lower target for this one check. `leave_out`: the "
                    "batch without this check (its runs skip it). `less_sure`: the whole "
                    "batch one step less sure.",
    )
    label: str = Field(description="The option in plain words, to show as it is.")
    check: CheckRef | None = Field(
        default=None,
        description="The check it changes; null for `less_sure` and for a leave-out of "
                    "several checks (see `checks`).")
    checks: list[CheckRef] = Field(
        default_factory=list,
        description="For a batch-level `leave_out`: the checks left out together — the "
                    "fewest that reach the goal when no single change does. Empty otherwise.")
    target: float | None = Field(default=None, description="For `lower_target`: the target.")
    confidence: float | None = Field(
        default=None, description="For `less_sure`: the confidence level.")
    times: int = Field(description="The size the batch would then default to.")
    chance: float = Field(description="Its chance that every check gets an answer.")
    reaches_goal: bool = Field(description="Whether that chance reaches the goal.")
    best_chance: float = Field(
        default=0.0,
        description="The best chance the change can get within one batch — the ceiling, "
                    "when it doesn't reach the goal.")
    best_times: int = Field(default=0, description="The size with `best_chance`.")
    judge_calls_saved_per_time: int = Field(
        default=0, description="For `leave_out`: judge calls one time no longer makes.")


class CheckPlan(BaseModel):
    label: str = Field(description="The check's label within its test.")
    test_type: str = Field(description="The catalogue type it runs.")
    applies: bool = Field(description="Whether the statistical test gives it a verdict.")
    reason: str | None = Field(description="Why not, when it doesn't.")
    target: float | None = Field(
        default=None,
        description="For a target test: the check's target — the batch's, or its own when "
                    "the request set one (`targets`).",
    )
    left_out: bool = Field(
        default=False, description="Left out of the batch by the request (`leave_out`).")
    history: CheckHistoryOut | None = Field(
        default=None, description="What earlier runs showed; null when none gave it a result.")
    outlook: Outlook | None = Field(
        default=None,
        description="How the check looks against its target: `likely_pass`, "
                    "`likely_fail` (each at least 9 chances in 10, from its history), "
                    "`too_close` (neither: expensive to tell either way), `unknown` (no "
                    "history), `certain` (it can't vary: a recorded answer read by a fixed "
                    "check). Null when the test doesn't apply to it.",
    )
    outlook_reason: str | None = Field(
        default=None, description="The outlook in one sentence, to show as it is.")
    certain_result: str | None = Field(
        default=None,
        description="For a `certain` check with a history: `pass` or `fail`, the result "
                    "every run gives.",
    )
    size_needed: int | None = Field(
        default=None,
        description="The fewest times for a `goal` chance of an answer for this check "
                    "alone; null when no size within one batch reaches it, or without "
                    "history.",
    )
    best_chance: float | None = Field(
        default=None,
        description="This check's best chance of an answer within one batch; null without "
                    "history.",
    )
    cheaper: list[CheaperOption] = Field(
        default_factory=list,
        description="Changes to this check that make the batch's answer more likely or "
                    "cheaper: lower targets, leaving it out.",
    )


class OddsPoint(BaseModel):
    times: int
    chance: float = Field(description="The chance that every check gets an answer.")


class TrialOffer(BaseModel):
    """A trial: the scope run a few times with no verdict, to learn how each
    check behaves before planning a batch."""
    statistical_test: str = Field(description="The trial's catalogue id, to send as is.")
    times: int = Field(description="Its default size.")
    application_calls: int = Field(description="What it costs: application calls.")
    judge_calls: int = Field(description="What it costs: judge calls.")
    reason: str = Field(description="Why it's offered, to show as it is.")


class EntryPlan(BaseModel):
    entry_id: uuid.UUID | None = Field(
        description="The test set entry; null for a standalone test.")
    test_id: uuid.UUID = Field(description="The live test it comes from.")
    test_set_id: uuid.UUID | None = Field(description="Its test set; null for a test.")
    test_set_name: str | None = Field(description="Its test set's name; null for a test.")
    name: str
    recorded_answer: bool = Field(
        description="Whether it has a recorded answer: then only its judge checks can vary "
                    "between runs.",
    )
    checks: list[CheckPlan]


class Estimate(BaseModel):
    """What a batch of this scope and test would need and cost."""
    scope: Scope
    statistical_test: str = Field(description="The catalogue row's id, as requested.")
    engine: StatisticalEngine = Field(description="The arithmetic that row runs.")
    parameters: dict[str, float] = Field(
        description="Every parameter, the defaults filled in.")
    floor: int = Field(description="The fewest times this test can conclude anything at.")
    floor_explanation: str = Field(description="Why, in one sentence, for these parameters.")
    suggestions: list[Suggestion] = Field(description="Sizes worth offering, smallest first.")
    times: int = Field(description="The times this estimate is for: yours, or the default.")
    rule: GateRuleSchema | None = Field(
        description="For the binomial gate: what passes and what fails at `times`. Null "
                    "for other tests.",
    )
    runs_per_time: int = Field(description="Runs one time creates: one per entry.")
    runs_total: int = Field(description="runs_per_time × times.")
    calls: Calls
    checks_total: int = Field(description="Checks across every entry.")
    checks_applicable: int = Field(description="Of those, the ones that get a verdict.")
    entries: list[EntryPlan] = Field(
        description="Every entry the batch would run, with each check and whether the "
                    "statistical test applies to it.",
    )
    warnings: list[Warning_] = Field(description="What to know before confirming.")
    goal: float = Field(
        default=0.9,
        description="The chance that every check gets an answer the default size aims for.")
    goal_reachable: bool | None = Field(
        default=None,
        description="Whether a size within one batch reaches `goal`; null when a check has "
                    "no history (run a trial first).",
    )
    best_chance: float | None = Field(
        default=None,
        description="The best chance that every check gets an answer within one batch; "
                    "null without history.",
    )
    best_times: int | None = Field(default=None, description="The size with `best_chance`.")
    odds_summary: str | None = Field(
        default=None,
        description="The odds in one sentence, to show as it is — e.g. \"Even 977 times "
                    "give every check an answer only 71% of the time; 715 times give 66% for "
                    "27% fewer runs.\" Null without history.",
    )
    driving_check: CheckRef | None = Field(
        default=None,
        description="The check that needs the most runs: the one to act on to make the "
                    "batch cheaper. Null without history.",
    )
    odds: list[OddsPoint] = Field(
        default_factory=list,
        description="Chart-ready: the chance that every check gets an answer, at the sizes "
                    "where one more failure becomes allowed (the curve's peaks), up to what "
                    "one batch can run. Empty without history.",
    )
    cheaper: list[CheaperOption] = Field(
        default_factory=list,
        description="Changes to the whole batch that make an answer more likely or cheaper "
                    "(one step less sure).",
    )
    trial: TrialOffer | None = Field(
        default=None,
        description="When a check has no history: a trial to run first, to learn how the "
                    "checks behave. Null otherwise.",
    )


# ── batches ──────────────────────────────────────────────────────────────────


class BatchStatusName(StrEnum):
    """A batch's status, as the API spells it (the same words as BatchStatus
    in models/statistics.py)."""
    pending = "Pending"
    running = "Running"
    passed = "Passed"
    failed = "Failed"
    inconclusive = "Inconclusive"
    incomplete = "Incomplete"
    not_ran = "NotRan"


class BatchRequest(EstimateRequest):
    """POST /statistics/batches: run a scope N times as one batch."""
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [
            {"test_set_id": "4e86003a-9e28-4c93-a08e-f99c6acbaab6",
             "statistical_test": "binomial_gate",
             "parameters": {"target": 0.9, "confidence": 0.95}, "times": 30,
             "note": "Prompt v3, temperature 0.2"},
            {"test_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
             "statistical_test": "one_sample_t", "parameters": {"difference": 0.05}},
        ]},
    )

    note: str | None = Field(
        None, max_length=500,
        description="Free text kept with the batch and shown in lists and comparisons — what "
                    "this batch is of (\"prompt v3, temperature 0.2\"), so A against B says "
                    "what changed between them. At most 500 characters; blank is stored as "
                    "null.",
    )


class BatchCallCount(BaseModel):
    planned: int = Field(description="Calls the whole batch was planned to make.")
    finished: int = Field(
        description="Calls made by runs that have finished: an application call per finished "
                    "run of an entry with no recorded answer, a judge call per judge check "
                    "of a finished run. Attempts count, failed ones too (they're paid for or "
                    "close to it); runs a stop cancelled don't. Retries aren't counted.",
    )
    in_flight: int = Field(
        description="Calls the runs executing right now are making or about to make.",
    )


class BatchCalls(BaseModel):
    application: BatchCallCount = Field(description="Calls to the application under test.")
    judge: BatchCallCount = Field(description="Calls to the judge model.")


class RunCounts(BaseModel):
    """Runs by status: every key always present, 0 when none."""
    Pending: int = 0
    Running: int = 0
    Green: int = 0
    Amber: int = 0
    Red: int = 0
    NotRan: int = 0


class SeriesPoint(BaseModel):
    """One run's outcome for one check — one point of a chart."""
    index: int = Field(
        description="The time this run belongs to, 1 to `times_requested`: the x axis. The "
                    "same index across every check and entry is the same time.",
    )
    run_id: uuid.UUID = Field(description="The run, to open it from a point.")
    execution_id: uuid.UUID | None = Field(
        description="The set or plan execution the run belongs to; null for a test's "
                    "standalone runs.",
    )
    status: str = Field(
        description="The run's status: `Green`, `Amber`, `Red`, or `NotRan` (then `passed` "
                    "and `score` are null and `error` says why).",
    )
    passed: bool | None = Field(
        description="Whether this check passed in this run; null when the run was Not Ran or "
                    "the check errored (it was never decided).",
    )
    score: float | None = Field(
        description="The check's score on its own scale; null for a pass/fail check, a Not "
                    "Ran run or an errored check.",
    )
    error: str | None = Field(
        description="Why there's no result: the run's own reason when it was Not Ran, or the "
                    "check's error (a judge timeout, no judge configured). Null otherwise.",
    )


class RunStripPoint(BaseModel):
    """One run of an entry, for the runs matrix (a row per entry, a column per
    time)."""
    index: int = Field(description="The time, 1 to `times_requested`.")
    run_id: uuid.UUID
    execution_id: uuid.UUID | None = Field(
        description="Its set or plan execution; null for a standalone run.")
    status: str = Field(description="The run's status, Pending and Running included.")


class EntryProgress(BaseModel):
    """One entry's runs while the batch runs: a row of the runs matrix before
    there is a result."""
    entry_id: uuid.UUID | None = Field(description="The test set entry; null for a test.")
    test_id: uuid.UUID | None = Field(description="The live test it was copied from.")
    test_set_id: uuid.UUID | None = Field(description="Its test set; null for a test.")
    test_set_name: str | None = Field(description="Its test set's name; null for a test.")
    name: str
    runs: RunCounts = Field(description="This entry's runs by status.")
    strip: list[RunStripPoint] = Field(
        description="This entry's runs by time, Pending and Running included.")


class BatchProgress(BaseModel):
    """How far the batch has got — what the running batch page shows beside
    Stop, so stopping is an informed choice."""
    times_requested: int = Field(description="Times the batch was created to run the scope.")
    times_done: int = Field(
        description="Times whose every run has finished (Green, Amber, Red or Not Ran).",
    )
    runs_total: int = Field(description="Runs the batch created: times × entries.")
    runs_done: int = Field(description="Runs that have finished, whatever their outcome.")
    runs: RunCounts = Field(description="Runs by status.")
    runs_cancelled: int = Field(
        description="Of the Not Ran runs, those a stop cancelled before they ran (counted in "
                    "`runs_done` too: they're finished, they'll never run).",
    )
    times_cancelled: int = Field(
        description="Times every run of which a stop cancelled: they never ran. "
                    "`times_requested − times_cancelled` is how many times actually ran.",
    )
    calls: BatchCalls = Field(description="What has been paid for so far, and what's planned.")
    entries: list[EntryProgress] | None = Field(
        default=None,
        description="On the batch read while the batch runs: the runs matrix as it fills in, "
                    "one row per entry in the result's entry order, each run's status by "
                    "time. Null in lists, with `series=false`, and once the result is "
                    "stored (`result.entries[].strip` is the finished matrix).",
    )


class Scale(BaseModel):
    """The check's native score range, from its type's threshold bounds — the
    chart's y axis."""
    min: float | None = Field(description="The lowest score the type gives (e.g. 0, or -1).")
    max: float | None = Field(description="The highest score the type gives (e.g. 1, or 100).")


class CheckCounts(BaseModel):
    """What the check's runs came to. `passed + failed` is the sample the
    statistic reads; errored and Not Ran runs are counted apart, since they
    say nothing about the check."""
    evaluated: int = Field(description="Runs in which the check was decided: passed + failed.")
    passed: int
    failed: int
    errored: int = Field(
        description="Runs in which the check itself errored (a judge timeout, no judge "
                    "chosen): never decided, so left out of the sample.",
    )
    not_ran: int = Field(description="Runs that were Not Ran as a whole: nothing evaluated.")


class ScoreSummaryOut(BaseModel):
    """The scores of the evaluated runs: what a box plot or an error bar
    needs, so the FE doesn't recompute them."""
    n: int = Field(description="Scores in the sample.")
    mean: float
    sd: float | None = Field(description="Sample standard deviation (n − 1); null for one score.")
    min: float
    max: float
    p10: float
    p25: float
    median: float
    p75: float
    p90: float


class CheckVerdict(StrEnum):
    passed = "pass"
    failed = "fail"
    inconclusive = "inconclusive"


class Statistic(BaseModel):
    """What the batch's statistical test concluded for one check."""
    verdict: CheckVerdict | None = Field(
        description="`pass`: proven at the confidence level. `fail`: the opposite proven. "
                    "`inconclusive`: neither at this size — `times_to_decide` says what "
                    "would. Null: no verdict could be drawn at all (no evaluated run, or a "
                    "stopped batch below the floor); `reason` says which.",
    )
    reason: str = Field(
        description="The verdict in one sentence, as the UI shows it (\"95% confident it "
                    "passes at least 90% of the time\").",
    )
    n: int = Field(description="The sample: runs in which the check was decided.")
    interval: Interval | None = Field(
        description="The interval the verdict was drawn from, so a whisker can never "
                    "contradict it: the two exact one-sided bounds of the pass rate for the "
                    "gate, the one-sided t bounds of the mean score for the t-test. Null "
                    "when there's no sample, or one score (no spread).",
    )
    p_value_pass: float | None = Field(
        description="The p-value of the test that would prove it passes (small = proven).",
    )
    p_value_fail: float | None = Field(
        description="The p-value of the test that would prove it fails (small = proven).",
    )
    rule: GateRuleSchema | None = Field(
        description="The binomial gate at this sample size: passes needed to pass, and at "
                    "or below which it fails. Null for the t-test.",
    )
    t: float | None = Field(None, description="t-test: the t statistic.")
    df: int | None = Field(None, description="t-test: degrees of freedom (n − 1).")
    standard_error: float | None = Field(None, description="t-test: sd / √n.")
    times_to_decide: int | None = Field(
        description="When inconclusive: the size of a **new** batch that would likely decide "
                    "it, if the check keeps behaving as it did here (never an extension of "
                    "this one: pooling batches until one passes would make the confidence "
                    "untrue). Null when the verdict is decided, or no size would.",
    )
    times_to_decide_message: str | None = Field(
        description="The same as a sentence, in the direction the data point: \"A new batch "
                    "of about 239 times would likely prove it below 90%\".",
    )


class CheckResult(BaseModel):
    """One check of one entry over the batch's runs: the frame a chart needs,
    the counts, the descriptive numbers, the verdict, and the series."""
    label: str = Field(description="The check's label within its test: its identity.")
    test_type: str = Field(description="The catalogue type it runs.")
    applies: bool = Field(description="Whether the batch's statistical test gives it a verdict.")
    reason: str | None = Field(description="Why not, when it doesn't.")
    scale: Scale | None = Field(
        description="The score range, for a check scored on a scale; null for pass/fail "
                    "checks.",
    )
    threshold: float | None = Field(
        description="The assignment's threshold, for a scored check: draw it as a line. Null "
                    "for pass/fail checks, or when it isn't a number.",
    )
    comparison: str | None = Field(
        description="Which side of the threshold passes: `gte` (at or above) or `lte`. Null "
                    "for pass/fail checks.",
    )
    target: float | None = Field(
        description="The binomial gate's target pass rate (or judge stability's target "
                    "agreement), for a line on the chart; null under other tests.",
    )
    counts: CheckCounts
    pass_rate: Interval | None = Field(
        description="The share of evaluated runs that passed, with its range: under the gate, "
                    "the two exact one-sided bounds the verdict uses; otherwise Wilson's "
                    "two-sided interval. Null with no evaluated run.",
    )
    agreement: Interval | None = Field(
        default=None,
        description="Judge stability only: the share of runs that gave the judge's usual "
                    "verdict, with the two exact one-sided bounds the verdict uses. Null "
                    "under other tests.",
    )
    scores: ScoreSummaryOut | None = Field(
        description="The scores' distribution, for a scored check with at least one score; "
                    "null otherwise.",
    )
    statistic: Statistic | None = Field(
        description="The statistical test's conclusion; null when it doesn't apply to this "
                    "check (see `reason`).",
    )
    series: list[SeriesPoint] | None = Field(
        description="Every run's outcome for this check, by `index`: a strip of passes and "
                    "fails, a score scatter, a histogram, a running pass rate. Null when "
                    "the read asked `series=false`.",
    )


class EntryResult(BaseModel):
    """One entry of the scope (the test itself for a standalone batch)."""
    entry_id: uuid.UUID | None = Field(description="The test set entry; null for a test.")
    test_id: uuid.UUID | None = Field(description="The live test it was copied from.")
    test_set_id: uuid.UUID | None = Field(description="Its test set; null for a test.")
    test_set_name: str | None = Field(description="Its test set's name; null for a test.")
    name: str
    recorded_answer: bool = Field(
        description="Whether it has a recorded answer: then only its judge checks can vary.",
    )
    runs: RunCounts = Field(description="This entry's runs by status.")
    checks: list[CheckResult] = Field(description="Every check, in label order.")
    strip: list[RunStripPoint] | None = Field(
        description="This entry's runs by time: a row of the runs matrix. Null when the read "
                    "asked `series=false`.",
    )


class EntryFailures(BaseModel):
    entry_id: uuid.UUID | None
    name: str
    test_set_name: str | None
    passed: int = Field(description="Runs in which every decided check passed.")
    failed: int = Field(description="Runs in which at least one decided check failed.")


class FailuresByEntry(BaseModel):
    """Do failures concentrate in some entries, or spread evenly? Pearson's
    chi-square on entries × (passed, failed) runs — a diagnostic beside the
    verdicts, for a set or plan batch (docs/statistics/dev_notes.md note 24)."""
    verdict: str | None = Field(
        description="`concentrated`: some entries fail significantly more than others — "
                    "look at them first. `no_evidence`: nothing says the failures cluster. "
                    "Null when there's nothing to locate (no run failed, or every run did).",
    )
    reason: str = Field(description="In one sentence, naming the entries that fail most.")
    chi_square: float | None
    df: int | None = Field(description="Entries − 1.")
    p_value: float | None
    approximate: bool = Field(
        description="True when an expected count is under 5: the p-value is then only "
                    "approximate.",
    )
    entries: list[EntryFailures] = Field(
        description="Each entry's runs that passed and failed (Not Ran runs, and runs whose "
                    "every check errored, left out), worst first.",
    )


class BatchResult(BaseModel):
    """The statistics, computed once every run has finished and stored with
    the batch: later reads return exactly this."""
    computed_at: datetime = Field(description="When it was computed.")
    checks_total: int
    checks_applicable: int = Field(description="Checks the statistical test gives a verdict.")
    verdicts: dict[str, int] = Field(
        description="Applicable checks by verdict: `pass`, `fail`, `inconclusive`, and "
                    "`none` (no verdict could be drawn).",
    )
    summary: str = Field(description="The batch's outcome in one sentence.")
    entries: list[EntryResult]
    failures_by_entry: FailuresByEntry | None = Field(
        default=None,
        description="For a batch of two or more entries: whether failures concentrate in "
                    "some of them. Null for a single test.",
    )


class BatchSummary(BaseModel):
    """A batch as lists show it: no per-check detail."""
    id: uuid.UUID
    scope: Scope
    statistical_test: str = Field(description="The catalogue row's id the batch was created with.")
    engine: StatisticalEngine = Field(
        description="The arithmetic that row named when the batch was created. The batch is "
                    "finished with it even if the row is edited or deleted later.",
    )
    parameters: dict[str, float] = Field(description="Every parameter, defaults filled in.")
    note: str | None
    status: BatchStatusName = Field(
        description="`Pending` (no run started), `Running`, then `Passed` (every applicable "
                    "check proven), `Failed` (at least one check proven to fail), "
                    "`Inconclusive` (finished, neither proven at this size), `Incomplete` "
                    "(stopped before every run ran) or `NotRan` (nothing could be decided: "
                    "every run Not Ran, or every check errored in every run — fix the cause, "
                    "a bigger batch wouldn't help).",
    )
    floor: int = Field(description="The fewest times the test can conclude at.")
    progress: BatchProgress
    summary: str | None = Field(description="The outcome in one sentence, once computed.")
    verdicts: dict[str, int] | None = Field(
        description="Applicable checks by verdict (`pass`, `fail`, `inconclusive`, `none`) "
                    "once computed — the same counts as `result.verdicts`, here for lists; "
                    "null while the batch runs.",
    )
    created_at: datetime
    stopped_at: datetime | None = Field(description="When a stop cancelled runs; null otherwise.")
    completed_at: datetime | None = Field(
        description="When the result was computed: every run had finished.",
    )
    targets: list[CheckTarget] = Field(
        default_factory=list, description="Checks run with their own target, as requested.")
    leave_out: list[CheckRef] = Field(
        default_factory=list, description="Checks left out of the batch, as requested.")


class BatchDetails(BatchSummary):
    """A batch with its result."""
    result: BatchResult | None = Field(
        description="Null while any run is Pending or Running — no verdict mid-batch: an "
                    "interim verdict invites stopping on a lucky streak. Computed and stored "
                    "by the first read that finds every run finished.",
    )


class BatchList(Pagination):
    items: list[BatchSummary]


# ── comparisons ──────────────────────────────────────────────────────────────


class ComparisonRequest(BaseModel):
    """POST /statistics/comparisons: two finished batches of the same scope,
    A the baseline and B the change."""
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [
            {"batch_a": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
             "batch_b": "2f1b6a3e-8d4c-4b9e-a1f0-5c3d2e1b0a99",
             "statistical_test": "pass_rates", "parameters": {"confidence": 0.95},
             "note": "Prompt v3 against v2"},
        ]},
    )

    batch_a: uuid.UUID = Field(description="The baseline batch: what B is compared against.")
    batch_b: uuid.UUID = Field(
        description="The batch to judge: every difference is B − A, so \"better\" means B "
                    "passes more often than A.",
    )
    statistical_test: str = Field(
        "pass_rates",
        description="The `id` of a comparison test from `GET /statistics/tests` "
                    "(`kind: comparison`); an unknown id is a 422.",
    )
    parameters: dict[str, float] = Field(
        default_factory=dict,
        description="The test's parameters by key; any left out take their default.",
    )
    note: str | None = Field(
        None, max_length=500,
        description="Free text kept with the comparison (\"prompt v3 against v2\"). At most "
                    "500 characters; blank is stored as null.",
    )


class ComparisonVerdictName(StrEnum):
    """`better` / `worse` / `no_difference` for the difference tests;
    `no_worse` / `worse` / `inconclusive` for the non-inferiority test."""
    better = "better"
    worse = "worse"
    no_difference = "no_difference"
    no_worse = "no_worse"
    inconclusive = "inconclusive"


class ComparisonSide(BaseModel):
    """One batch's side of a check: its counts, its pass rate, its runs."""
    counts: CheckCounts
    pass_rate: Interval | None = Field(
        description="The batch's pass rate for this check with Wilson's two-sided interval; "
                    "null with no evaluated run.",
    )
    scores: ScoreSummaryOut | None = Field(
        default=None,
        description="The scores' distribution, for a scored check: two box plots side by "
                    "side. Null for a pass/fail check.",
    )
    series: list[SeriesPoint] | None = Field(
        description="The batch's runs for this check, by time — draw the two strips or "
                    "distributions over each other. Null when the read asked `series=false`.",
    )


class CheckComparison(BaseModel):
    """One check, B against A."""
    label: str = Field(description="The check's label: its identity in both batches.")
    test_type: str
    a: ComparisonSide
    b: ComparisonSide
    difference: Interval | None = Field(
        description="B − A with the interval the verdict is read from: the one bar with "
                    "whiskers to draw. Pass rates (`pass_rates`, `paired_entries`): Newcombe's "
                    "two-sided interval; `no_worse`: Newcombe's one-sided bounds (`sides: "
                    "one`); `mean_scores`: Welch's t interval of the mean scores. Null for "
                    "`score_ranks` (ranks have no difference to draw: see `effect`), or when "
                    "either side has nothing to compare.",
    )
    effect: float | None = Field(
        default=None,
        description="`score_ranks` only: the probability that a score of B beats one of A "
                    "(ties count half). 0.5 is no difference; 1 is B always higher.",
    )
    verdict: ComparisonVerdictName | None = Field(
        description="`better`: B did better — the difference's interval lies above 0 "
                    "(pass rates, mean scores), or B's scores rank significantly higher "
                    "(score ranks). `worse`: the other way. `no_difference`: neither can be "
                    "told at this size (not proof that there is none). For `no_worse`: "
                    "`no_worse` (proven at most `margin` worse), `worse` (proven more than "
                    "`margin` worse) or `inconclusive`. A lower-is-better score type turns "
                    "a rise into `worse`. Null when there's nothing to compare (`reason` "
                    "says why), and on every check under `paired_entries`, whose one "
                    "verdict is `result.paired.verdict`.",
    )
    reason: str = Field(description="The verdict in one sentence, as the UI shows it.")
    p_value: float | None = Field(
        description="Shown beside the verdict: chi-square's when every expected count is "
                    "at least 5, Fisher's exact otherwise (pass rates); Welch's (mean "
                    "scores); Mann–Whitney's (score ranks, where it does decide, with "
                    "`effect`). Null for `no_worse`, whose interval is the whole test. "
                    "`p_value_method` names it.",
    )
    p_value_method: str | None = Field(
        description="How `p_value` was computed — name it in the caption: `chi_square` or "
                    "`fisher_exact` (pass rates), `welch`, `mann_whitney_exact` or "
                    "`mann_whitney_normal`.",
    )
    times_to_decide: int | None = Field(
        description="When undecided (`no_difference`, or `inconclusive` for `no_worse`): "
                    "about how many times *each* of two new batches would need to decide it, "
                    "80% of the time, if the checks behave as they did here. Null otherwise.",
    )
    times_to_decide_message: str | None = Field(
        description="The same as a sentence.",
    )


class EntryComparison(BaseModel):
    entry_id: uuid.UUID | None = Field(description="The test set entry; null for a test.")
    test_id: uuid.UUID | None
    test_set_name: str | None
    name: str
    checks: list[CheckComparison] = Field(description="Every check in both, in label order.")


class UnmatchedSide(StrEnum):
    a = "a"
    b = "b"


class Unmatched(BaseModel):
    """An entry or check present in one batch only — the scope changed between
    them (an entry added to or removed from the set, a check relabelled)."""
    entry_id: uuid.UUID | None
    name: str = Field(description="The entry's name.")
    label: str | None = Field(description="The check; null when the whole entry is unmatched.")
    only_in: UnmatchedSide = Field(description="The batch it's in: `a` or `b`.")


class Pair(BaseModel):
    """One (entry, check) under both batches: a point of the paired chart."""
    entry_id: uuid.UUID | None
    name: str
    label: str
    rate_a: float
    rate_b: float
    difference: float = Field(description="rate_b − rate_a.")


class PairedComparison(BaseModel):
    """`paired_entries`: every (entry, check) both batches evaluated, paired
    with itself — one verdict for the whole scope."""
    n_pairs: int
    pairs: list[Pair] = Field(description="Every pair, in entry then label order.")
    difference: Interval | None = Field(
        description="The mean per-pair difference B − A with the paired t interval "
                    "(two-sided): what the verdict is read from.",
    )
    verdict: ComparisonVerdictName | None
    reason: str
    p_value: float | None = Field(description="The paired t-test's two-sided p-value.")
    p_value_wilcoxon: float | None = Field(
        description="Wilcoxon's signed-rank p-value, beside it: it doesn't assume the "
                    "differences are bell-shaped. When the two disagree, say so.",
    )
    wilcoxon_method: str | None = Field(description="`exact` or `normal`.")


class ComparisonResult(BaseModel):
    verdicts: dict[str, int] = Field(
        description="Checks by verdict: `better`, `worse`, `no_difference`, `no_worse`, "
                    "`inconclusive`, and `none` (no verdict). For `paired_entries`, the one "
                    "paired verdict.",
    )
    summary: str = Field(description="The comparison in one sentence.")
    entries: list[EntryComparison]
    unmatched: list[Unmatched] = Field(
        description="What only one batch has: not compared, listed so nothing goes missing "
                    "silently.",
    )
    paired: PairedComparison | None = Field(
        default=None,
        description="`paired_entries` only: the verdict over every pair. The per-check items "
                    "above then carry each pair's numbers without a verdict of their own.",
    )


class ComparedBatch(BaseModel):
    """A batch as a comparison names it."""
    id: uuid.UUID
    note: str | None
    status: BatchStatusName
    statistical_test: str
    times_requested: int
    created_at: datetime


class ComparisonSummary(BaseModel):
    id: uuid.UUID
    scope: Scope
    statistical_test: str = Field(description="The catalogue row's id the comparison ran.")
    engine: StatisticalEngine = Field(description="The arithmetic that row named at the time.")
    parameters: dict[str, float]
    note: str | None
    batch_a: ComparedBatch = Field(description="The baseline.")
    batch_b: ComparedBatch = Field(description="The change: differences are B − A.")
    summary: str
    created_at: datetime


class ComparisonDetails(ComparisonSummary):
    result: ComparisonResult


class ComparisonList(Pagination):
    items: list[ComparisonSummary]
