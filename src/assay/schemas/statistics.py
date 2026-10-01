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
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── shared vocabulary ────────────────────────────────────────────────────────


class StatisticalTestName(StrEnum):
    """Every statistical test Assay knows. `kind` in the catalogue says which
    are run as a batch and which compare two batches."""
    binomial_gate = "binomial_gate"
    one_sample_t = "one_sample_t"
    pass_rates = "pass_rates"


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
                    "a t-test).",
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
    id: StatisticalTestName = Field(description="What to send as `statistical_test`.")
    name: str = Field(description="Its name, e.g. \"Binomial gate\".")
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
                    "test, `better`/`worse`/`no_difference` for a comparison.",
    )
    parameters: list[ParameterDescriptor] = Field(
        description="What can be set, each with its default and range. Send them under "
                    "`parameters`; any left out take the default.",
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

    statistical_test: StatisticalTestName = Field(
        description="A batch test from the catalogue (`kind: batch`).",
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


class SuggestionKind(StrEnum):
    floor = "floor"
    absorbs_not_ran = "absorbs_not_ran"
    allows_one_miss = "allows_one_miss"
    detects_difference = "detects_difference"
    recommended = "recommended"


class Suggestion(BaseModel):
    times: int = Field(description="A number of times worth offering.")
    kind: SuggestionKind = Field(
        description="Why: `floor` (the least that can conclude), `absorbs_not_ran` (one "
                    "more, so a run that can't be evaluated doesn't cost the verdict), "
                    "`allows_one_miss` (the gate still passes with one failure), "
                    "`detects_difference` (enough to see the difference you set, 80% of the "
                    "time), `recommended` (where the test's maths is comfortable).",
    )
    label: str = Field(description="A short caption, e.g. \"29 · no miss allowed\".")
    default: bool = Field(description="The one used when `times` is left out.")


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


class CheckPlan(BaseModel):
    label: str = Field(description="The check's label within its test.")
    test_type: str = Field(description="The catalogue type it runs.")
    applies: bool = Field(description="Whether the statistical test gives it a verdict.")
    reason: str | None = Field(description="Why not, when it doesn't.")


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
    statistical_test: StatisticalTestName
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
