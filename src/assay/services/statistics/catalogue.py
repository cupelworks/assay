"""The catalogue of statistical tests, as code (docs/statistics/dev_notes.md
note 5): what each one asks, the parameters it takes with their defaults and
ranges, its floor and where the floor comes from, and the sizes worth
suggesting. The catalogue endpoint, the estimate and batch creation all read
it, so the numbers the user is shown are the numbers enforced.
"""
from dataclasses import dataclass

from fastapi.exceptions import RequestValidationError

from assay import stats_math
from assay.schemas.statistics import (
    AppliesTo,
    FloorDescriptor,
    FloorKind,
    ParameterDescriptor,
    ParameterKind,
    Reads,
    StatisticalTestCatalogue,
    StatisticalTestDescriptor,
    StatisticalTestKind,
    StatisticalTestName,
    Suggestion,
    SuggestionKind,
)

# A batch is history (it can't be deleted), and every run of it is paid for:
# these bound what one request can create (dev_notes.md note 20).
MAX_TIMES = 1000
MAX_RUNS = 10_000
# Statistical power for every "enough to see it" suggestion: an 80% chance of
# detecting a difference that is really there, the usual choice.
POWER = 0.8
# The t-test's floor and comfortable size (note 4, kind 3)
T_TEST_FLOOR = 10
T_TEST_RECOMMENDED = 30

_CONFIDENCE = ParameterDescriptor(
    key="confidence", label="Confidence level", kind=ParameterKind.level, default=0.95,
    min=0.80, max=0.999,
    hint="How sure a verdict must be: at 0.95 a proven claim is wrong at most 1 time in 20. "
         "Higher needs more runs.",
)


@dataclass(frozen=True)
class CatalogueEntry:
    descriptor: StatisticalTestDescriptor

    @property
    def name(self) -> StatisticalTestName:
        return self.descriptor.id

    @property
    def kind(self) -> StatisticalTestKind:
        return self.descriptor.kind


_GATE = StatisticalTestDescriptor(
    id=StatisticalTestName.binomial_gate,
    name="Binomial gate",
    kind=StatisticalTestKind.batch,
    wave=1,
    question="Does each check pass at least a target share of the time? For example: "
             "\"95% confident it passes at least 90% of the time\".",
    reads=Reads.pass_fail,
    applies_to=AppliesTo.every_check,
    verdicts=["pass", "fail", "inconclusive"],
    parameters=[
        ParameterDescriptor(
            key="target", label="Target pass rate", kind=ParameterKind.rate, default=0.9,
            min=0.5, max=0.999,
            hint="The share of runs each check must pass: 0.9 means \"at least 90% of the "
                 "time\". Higher targets need many more runs.",
        ),
        _CONFIDENCE,
    ],
    floor=FloorDescriptor(
        kind=FloorKind.exact,
        formula="times ≥ ln(1 − confidence) / ln(target)",
        explanation="If a check really passed exactly the target share of the time, how "
                    "likely would it be to pass every run? At 90% that's 0.9^29 = 4.7% for "
                    "29 runs — rarer than 1 in 20 — so 29 straight passes prove \"above "
                    "90%\" with 95% confidence. At 28 runs it's 5.2%: no result could prove "
                    "it. The floor allows no miss; one miss takes 46 runs at 90%.",
        examples=[
            {"target": 0.8, "confidence": 0.95, "times": 14},
            {"target": 0.9, "confidence": 0.95, "times": 29},
            {"target": 0.95, "confidence": 0.95, "times": 59},
            {"target": 0.99, "confidence": 0.95, "times": 299},
        ],
    ),
    recommended_times=None,
    method="An exact binomial test each way at the confidence level: pass when the exact "
           "(Clopper–Pearson) one-sided lower bound of the pass rate is at least the "
           "target, fail when the upper bound is below it, inconclusive otherwise.",
)

_T_TEST = StatisticalTestDescriptor(
    id=StatisticalTestName.one_sample_t,
    name="One-sample t-test",
    kind=StatisticalTestKind.batch,
    wave=1,
    question="Is each scored check's average score on the passing side of its threshold? "
             "For example: \"95% confident the average ROUGE is above 0.6\".",
    reads=Reads.scores,
    applies_to=AppliesTo.scored_checks,
    verdicts=["pass", "fail", "inconclusive"],
    parameters=[
        _CONFIDENCE,
        ParameterDescriptor(
            key="difference", label="Smallest gap worth detecting", kind=ParameterKind.share,
            default=0.05, min=0.005, max=1.0,
            hint="How far from the threshold an average must be for you to care, as a share "
                 "of the check's score range (0.05 = 0.05 on ROUGE, 5 points on BLEU). "
                 "Only sizes the suggestion.",
        ),
        ParameterDescriptor(
            key="spread", label="Expected spread of scores", kind=ParameterKind.share,
            default=0.1, min=0.001, max=1.0,
            hint="How much scores usually vary between runs (their standard deviation), as a "
                 "share of the score range. Only sizes the suggestion; the verdict uses the "
                 "spread the batch observes.",
        ),
    ],
    floor=FloorDescriptor(
        kind=FloorKind.rule_of_thumb,
        formula=None,
        explanation="Below 10 scores the spread is too poorly known for the t-test to mean "
                    "much; 30 is where it's comfortable. The suggestion in between sizes the "
                    "batch to see the gap you set: with scores spreading ±0.1, a mean 0.05 "
                    "from the threshold needs 27 runs to be seen 80% of the time.",
        examples=[
            {"difference": 0.05, "spread": 0.1, "confidence": 0.95, "times": 27},
            {"difference": 0.025, "spread": 0.1, "confidence": 0.95, "times": 101},
        ],
    ),
    recommended_times=T_TEST_RECOMMENDED,
    method="Student's one-sample t-test each way, in the direction of the check type's "
           "comparison: pass when the one-sided t bound of the mean score is on the passing "
           "side of the threshold, fail when the other bound is on the failing side, "
           "inconclusive otherwise. Scores that never vary (a recorded answer) are compared "
           "to the threshold directly.",
)

_PASS_RATES = StatisticalTestDescriptor(
    id=StatisticalTestName.pass_rates,
    name="Pass rates, A against B",
    kind=StatisticalTestKind.comparison,
    wave=1,
    question="Did a check's pass rate change between two batches of the same scope? For "
             "example: \"did my change to the prompt help?\".",
    reads=Reads.pass_fail,
    applies_to=AppliesTo.every_check,
    verdicts=["better", "worse", "no_difference"],
    parameters=[_CONFIDENCE],
    floor=FloorDescriptor(
        kind=FloorKind.none,
        formula=None,
        explanation="No minimum: with few runs Fisher's exact test stands in for "
                    "chi-square. But small batches only see big differences — 20 runs each "
                    "sees about 35 points, 60 each about 20 points, and 80% against 90% "
                    "takes about 200 each.",
        examples=[{"rate_a": 0.8, "rate_b": 0.9, "confidence": 0.95, "times": 199}],
    ),
    recommended_times=None,
    method="Newcombe's score interval of B − A decides: better when it lies above 0, worse "
           "when below, no real difference otherwise. Beside it, chi-square's p-value when "
           "every expected count is at least 5, Fisher's exact otherwise.",
)

CATALOGUE: dict[StatisticalTestName, CatalogueEntry] = {
    entry.id: CatalogueEntry(entry) for entry in (_GATE, _T_TEST, _PASS_RATES)
}


def catalogue() -> StatisticalTestCatalogue:
    return StatisticalTestCatalogue(
        items=[entry.descriptor for entry in CATALOGUE.values()],
        max_times=MAX_TIMES, max_runs=MAX_RUNS,
    )


def percent(rate: float) -> str:
    """0.9 → "90%", 0.995 → "99.5%"."""
    return f"{round(rate * 100, 3):g}%"


def _invalid(problems: list[tuple[tuple[str, ...], str]]) -> RequestValidationError:
    """A 422 in FastAPI's own list shape, one item per problem — the shape a
    body validation error has, so the FE handles it the same way."""
    return RequestValidationError([
        {"type": "value_error", "loc": ("body", *loc), "msg": msg} for loc, msg in problems
    ])


def resolve_parameters(name: StatisticalTestName, given: dict[str, float],
                       kind: StatisticalTestKind) -> dict[str, float]:
    """The test's parameters with defaults filled in, every value checked
    against its range — all problems reported together as a 422."""
    entry = CATALOGUE[name]
    problems: list[tuple[tuple[str, ...], str]] = []
    if entry.kind != kind:
        what = "a batch" if kind == StatisticalTestKind.batch else "a comparison"
        problems.append((("statistical_test",),
                         f"{entry.descriptor.name} isn't {what} test; see GET /statistics/tests"))
        raise _invalid(problems)
    known = {parameter.key: parameter for parameter in entry.descriptor.parameters}
    for key in sorted(set(given) - set(known)):
        problems.append((("parameters", key),
                         f"Unknown parameter for {entry.descriptor.name}; it takes "
                         f"{', '.join(sorted(known))}"))
    resolved = {}
    for key, parameter in known.items():
        value = given.get(key, parameter.default)
        if not parameter.min <= value <= parameter.max:
            problems.append((("parameters", key),
                             f"Must be between {parameter.min:g} and {parameter.max:g}"))
        resolved[key] = value
    if problems:
        raise _invalid(problems)
    return resolved


@dataclass(frozen=True)
class Sizing:
    """The floor, its explanation, and the sizes worth suggesting."""
    floor: int
    explanation: str
    suggestions: list[Suggestion]

    @property
    def default(self) -> int:
        return next(s.times for s in self.suggestions if s.default)


def sizing(name: StatisticalTestName, parameters: dict[str, float]) -> Sizing:
    confidence = parameters["confidence"]
    if name == StatisticalTestName.binomial_gate:
        target = parameters["target"]
        floor = stats_math.binomial_floor(target, confidence)
        one_miss = stats_math.binomial_runs_allowing(1, target, confidence)
        explanation = (
            f"At {floor} times only a perfect record proves \"at least {percent(target)}\" "
            f"with {percent(confidence)} confidence: {target:g}^{floor} = "
            f"{target ** floor:.4f} is at most {1 - confidence:.4g}. With fewer, no result "
            f"could prove it."
        )
        return Sizing(floor, explanation, [
            Suggestion(times=floor, kind=SuggestionKind.floor,
                       label=f"{floor} · no miss allowed", default=True),
            Suggestion(times=floor + 1, kind=SuggestionKind.absorbs_not_ran,
                       label=f"{floor + 1} · absorbs one Not Ran", default=False),
            Suggestion(times=one_miss, kind=SuggestionKind.allows_one_miss,
                       label=f"{one_miss} · allows one miss", default=False),
        ])
    if name == StatisticalTestName.one_sample_t:
        detect = stats_math.runs_needed_for_mean(
            parameters["spread"], parameters["difference"], confidence, POWER)
        detect = max(detect, T_TEST_FLOOR)
        explanation = (
            f"Below {T_TEST_FLOOR} scores the spread is too poorly known for a t-test; "
            f"{detect} times would see a mean {parameters['difference']:g} of the range from "
            f"the threshold 80% of the time, with scores spreading "
            f"{parameters['spread']:g} of the range."
        )
        candidates = [
            Suggestion(times=T_TEST_FLOOR, kind=SuggestionKind.floor,
                       label=f"{T_TEST_FLOOR} · the least that means anything", default=False),
            Suggestion(times=detect, kind=SuggestionKind.detects_difference,
                       label=f"{detect} · sees a gap of {parameters['difference']:g}",
                       default=True),
            Suggestion(times=T_TEST_RECOMMENDED, kind=SuggestionKind.recommended,
                       label=f"{T_TEST_RECOMMENDED} · comfortable", default=False),
        ]
        # one suggestion per size, the default kept when sizes coincide
        by_times: dict[int, Suggestion] = {}
        for suggestion in candidates:
            if suggestion.times not in by_times or suggestion.default:
                by_times[suggestion.times] = suggestion
        return Sizing(T_TEST_FLOOR, explanation,
                      [by_times[times] for times in sorted(by_times)])
    raise ValueError(f"{name} has no batch sizing")  # comparisons aren't sized here


def check_times(times: int, runs_per_time: int, floor: int, name: StatisticalTestName,
                parameters: dict[str, float]) -> None:
    """422 when the times are below the floor or above what one batch may
    create."""
    problems = []
    if floor > MAX_TIMES:
        problems.append((("parameters",),
                         f"These parameters need at least {floor} times, more than the "
                         f"{MAX_TIMES} a batch can run: lower the target or the confidence"))
    elif times < floor:
        what = CATALOGUE[name].descriptor.name
        problems.append((("times",),
                         f"At least {floor} times for the {what} with these parameters: below "
                         f"that no result could conclude anything"))
    elif times > MAX_TIMES:
        problems.append((("times",), f"At most {MAX_TIMES} times per batch"))
    elif times * runs_per_time > MAX_RUNS:
        problems.append((("times",),
                         f"{times} times × {runs_per_time} runs each is "
                         f"{times * runs_per_time} runs, more than the {MAX_RUNS} a batch can "
                         f"create: at most {MAX_RUNS // runs_per_time} times for this scope"))
    if problems:
        raise _invalid(problems)
