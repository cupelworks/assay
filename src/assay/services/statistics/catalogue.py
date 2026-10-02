"""The catalogue of statistical tests: engines in code, rows configuring them.

An engine (ENGINES, one per StatisticalEngine) is the arithmetic and its
fixed shape — its kind, what it reads, which checks it applies to, its
verdicts, the floor's kind and formula, the parameter keys it needs and the
settings it reads. A `statistical_tests` row (StatisticalTestModel) names an
engine and carries everything a user may want different: the texts, each
parameter's label, default, range and hint, the worked examples, the
engine's settings. The catalogue endpoint, the estimate and batch creation
all read the rows, so the numbers the user is shown are the numbers
enforced; a batch records its row's id and engine and is finished with the
engine, so a row edited later never changes a stored batch.
"""
from dataclasses import dataclass

from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assay import stats_math
from assay.models import StatisticalTestModel
from assay.schemas.statistics import (
    AppliesTo,
    FloorDescriptor,
    FloorKind,
    ParameterDescriptor,
    ParameterKind,
    Reads,
    StatisticalEngine,
    StatisticalTestCatalogue,
    StatisticalTestDescriptor,
    StatisticalTestKind,
    Suggestion,
    SuggestionKind,
)

# A batch is history (it can't be deleted), and every run of it is paid for:
# these bound what one request can create.
MAX_TIMES = 1000
MAX_RUNS = 10_000
# Statistical power for every "enough to see it" suggestion: an 80% chance of
# detecting a difference that is really there, the usual choice.
POWER = 0.8

_EXACT_FLOOR = "times ≥ ln(1 − confidence) / ln(target)"
_THREE_WAY = ("pass", "fail", "inconclusive")
_DIFFERENCE = ("better", "worse", "no_difference")


@dataclass(frozen=True)
class Engine:
    """The fixed shape of a statistical test: what its arithmetic in code
    needs from a row and gives. A row can't contradict it."""
    id: StatisticalEngine
    kind: StatisticalTestKind
    wave: int
    reads: Reads
    applies_to: AppliesTo
    verdicts: tuple[str, ...]
    # the parameters a row must define, each with the kind the engine reads it as
    parameters: dict[str, ParameterKind]
    floor_kind: FloorKind
    floor_formula: str | None
    # the engine_settings a row may carry, and whether each is required
    settings: dict[str, bool]


ENGINES: dict[StatisticalEngine, Engine] = {engine.id: engine for engine in (
    Engine(
        id=StatisticalEngine.binomial_gate, kind=StatisticalTestKind.batch, wave=1,
        reads=Reads.pass_fail, applies_to=AppliesTo.every_check, verdicts=_THREE_WAY,
        parameters={"target": ParameterKind.rate, "confidence": ParameterKind.level},
        floor_kind=FloorKind.exact, floor_formula=_EXACT_FLOOR, settings={},
    ),
    Engine(
        id=StatisticalEngine.one_sample_t, kind=StatisticalTestKind.batch, wave=1,
        reads=Reads.scores, applies_to=AppliesTo.scored_checks, verdicts=_THREE_WAY,
        parameters={"confidence": ParameterKind.level, "difference": ParameterKind.share,
                    "spread": ParameterKind.share},
        floor_kind=FloorKind.rule_of_thumb, floor_formula=None,
        settings={"floor": True, "recommended_times": True},
    ),
    Engine(
        id=StatisticalEngine.judge_stability, kind=StatisticalTestKind.batch, wave=2,
        reads=Reads.pass_fail, applies_to=AppliesTo.judge_checks, verdicts=_THREE_WAY,
        parameters={"target": ParameterKind.rate, "confidence": ParameterKind.level},
        floor_kind=FloorKind.exact, floor_formula=_EXACT_FLOOR, settings={},
    ),
    Engine(
        id=StatisticalEngine.pass_rates, kind=StatisticalTestKind.comparison, wave=1,
        reads=Reads.pass_fail, applies_to=AppliesTo.every_check, verdicts=_DIFFERENCE,
        parameters={"confidence": ParameterKind.level},
        floor_kind=FloorKind.none, floor_formula=None, settings={},
    ),
    Engine(
        id=StatisticalEngine.no_worse, kind=StatisticalTestKind.comparison, wave=2,
        reads=Reads.pass_fail, applies_to=AppliesTo.every_check,
        verdicts=("no_worse", "worse", "inconclusive"),
        parameters={"margin": ParameterKind.share, "confidence": ParameterKind.level},
        floor_kind=FloorKind.none,
        floor_formula="n ≈ (z₁₋α + z₀.₈)² · (pₐ(1 − pₐ) + p_b(1 − p_b)) / (p_b − pₐ + margin)²",
        settings={},
    ),
    Engine(
        id=StatisticalEngine.mean_scores, kind=StatisticalTestKind.comparison, wave=2,
        reads=Reads.scores, applies_to=AppliesTo.scored_checks, verdicts=_DIFFERENCE,
        parameters={"confidence": ParameterKind.level},
        floor_kind=FloorKind.rule_of_thumb, floor_formula=None,
        settings={"recommended_times": False},
    ),
    Engine(
        id=StatisticalEngine.score_ranks, kind=StatisticalTestKind.comparison, wave=2,
        reads=Reads.scores, applies_to=AppliesTo.scored_checks, verdicts=_DIFFERENCE,
        parameters={"confidence": ParameterKind.level},
        floor_kind=FloorKind.exact, floor_formula=None,
        settings={"recommended_times": False},
    ),
    Engine(
        id=StatisticalEngine.paired_entries, kind=StatisticalTestKind.comparison, wave=2,
        reads=Reads.pass_fail, applies_to=AppliesTo.every_check, verdicts=_DIFFERENCE,
        parameters={"confidence": ParameterKind.level},
        floor_kind=FloorKind.exact, floor_formula=None, settings={},
    ),
)}


class CatalogueError(ValueError):
    """A statistical_tests row the code can't run: an unknown engine,
    parameters that aren't the engine's, settings it doesn't read. Rows are
    seeded and edited server-side, so this is an operator's mistake to see at
    once — a 500 naming the row — never a silently missing test."""


@dataclass(frozen=True)
class CatalogueEntry:
    """A row paired with its engine and checked: what a request's
    `statistical_test` resolves to."""
    row: StatisticalTestModel
    engine: Engine
    parameters: tuple[ParameterDescriptor, ...]

    @property
    def id(self) -> str:
        return self.row.id

    @property
    def name(self) -> str:
        return self.row.name

    @property
    def kind(self) -> StatisticalTestKind:
        return self.engine.kind

    @property
    def settings(self) -> dict:
        return self.row.engine_settings or {}

    def descriptor(self) -> StatisticalTestDescriptor:
        return StatisticalTestDescriptor(
            id=self.id, name=self.name, engine=self.engine.id, kind=self.kind,
            wave=self.engine.wave, question=self.row.question, reads=self.engine.reads,
            applies_to=self.engine.applies_to, verdicts=list(self.engine.verdicts),
            parameters=list(self.parameters), engine_settings=self.settings,
            floor=FloorDescriptor(kind=self.engine.floor_kind,
                                  formula=self.engine.floor_formula,
                                  explanation=self.row.floor_explanation,
                                  examples=self.row.floor_examples or []),
            recommended_times=self.settings.get("recommended_times"), method=self.row.method,
        )


def entry_of(row: StatisticalTestModel) -> CatalogueEntry:
    """Pair a row with its engine, refusing a row the code can't run."""
    try:
        engine = ENGINES[StatisticalEngine(row.engine)]
    except ValueError:
        raise CatalogueError(f"Statistical test '{row.id}' names an engine the code doesn't "
                             f"have: '{row.engine}'") from None
    try:
        parameters = tuple(ParameterDescriptor(**item) for item in row.parameters or [])
    except (TypeError, ValidationError) as error:
        raise CatalogueError(f"Statistical test '{row.id}' has a malformed parameter: "
                             f"{error}") from None
    keys = [parameter.key for parameter in parameters]
    if sorted(keys) != sorted(engine.parameters):
        raise CatalogueError(f"Statistical test '{row.id}' defines the parameters {keys}; its "
                             f"engine '{engine.id.value}' takes exactly "
                             f"{sorted(engine.parameters)}")
    for parameter in parameters:
        expected = engine.parameters[parameter.key]
        if parameter.kind != expected:
            raise CatalogueError(f"Statistical test '{row.id}': parameter '{parameter.key}' is a "
                                 f"{expected.value} to its engine, not a {parameter.kind.value}")
        if not parameter.min <= parameter.default <= parameter.max:
            raise CatalogueError(f"Statistical test '{row.id}': parameter '{parameter.key}' "
                                 f"defaults to {parameter.default:g}, outside "
                                 f"{parameter.min:g}–{parameter.max:g}")
    settings = row.engine_settings or {}
    unknown = sorted(set(settings) - set(engine.settings))
    missing = sorted(key for key, required in engine.settings.items()
                     if required and key not in settings)
    if unknown or missing:
        raise CatalogueError(f"Statistical test '{row.id}': its engine '{engine.id.value}' "
                             f"reads the settings {sorted(engine.settings)}; "
                             f"unknown {unknown}, missing {missing}")
    for key, value in settings.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 2:
            raise CatalogueError(f"Statistical test '{row.id}': setting '{key}' must be a whole "
                                 f"number of at least 2, not {value!r}")
    if settings.get("recommended_times", settings.get("floor", 0)) < settings.get("floor", 0):
        raise CatalogueError(f"Statistical test '{row.id}': recommended_times "
                             f"({settings['recommended_times']}) is below the floor "
                             f"({settings['floor']})")
    return CatalogueEntry(row, engine, parameters)


async def load_catalogue(session: AsyncSession) -> list[CatalogueEntry]:
    """Every row with its engine: batch tests first, then in the order the
    rows were added."""
    rows = (await session.scalars(
        select(StatisticalTestModel)
        .order_by(StatisticalTestModel.created_at, StatisticalTestModel.id))).all()
    entries = [entry_of(row) for row in rows]
    return sorted(entries, key=lambda entry: entry.kind != StatisticalTestKind.batch)


async def load_entry(test_id: str, kind: StatisticalTestKind,
                     session: AsyncSession) -> CatalogueEntry:
    """The row a request names, as a test of this kind — a 422 for an unknown
    id or a test of the other kind, in FastAPI's list shape."""
    row = await session.get(StatisticalTestModel, test_id)
    if row is None:
        raise _invalid([(("statistical_test",),
                         f"Unknown statistical test '{test_id}'; see GET /statistics/tests")])
    entry = entry_of(row)
    if entry.kind != kind:
        what = "a batch" if kind == StatisticalTestKind.batch else "a comparison"
        raise _invalid([(("statistical_test",),
                         f"{entry.name} isn't {what} test; see GET /statistics/tests")])
    return entry


def catalogue(entries: list[CatalogueEntry]) -> StatisticalTestCatalogue:
    return StatisticalTestCatalogue(
        items=[entry.descriptor() for entry in entries],
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


def resolve_parameters(entry: CatalogueEntry, given: dict[str, float]) -> dict[str, float]:
    """The test's parameters with the row's defaults filled in, every value
    checked against its range — all problems reported together as a 422."""
    known = {parameter.key: parameter for parameter in entry.parameters}
    problems: list[tuple[tuple[str, ...], str]] = []
    for key in sorted(set(given) - set(known)):
        problems.append((("parameters", key),
                         f"Unknown parameter for {entry.name}; it takes "
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


def sizing(entry: CatalogueEntry, parameters: dict[str, float]) -> Sizing:
    engine = entry.engine.id
    confidence = parameters["confidence"]
    if engine in (StatisticalEngine.binomial_gate, StatisticalEngine.judge_stability):
        target = parameters["target"]
        floor = stats_math.binomial_floor(target, confidence)
        one_miss = stats_math.binomial_runs_allowing(1, target, confidence)
        perfect = ("a perfect record" if engine == StatisticalEngine.binomial_gate
                   else "every run agreeing")
        claim = ("at least" if engine == StatisticalEngine.binomial_gate
                 else "agrees at least")
        explanation = (
            f"At {floor} times only {perfect} proves \"{claim} {percent(target)}\" "
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
    if engine == StatisticalEngine.one_sample_t:
        floor, recommended = entry.settings["floor"], entry.settings["recommended_times"]
        detect = stats_math.runs_needed_for_mean(
            parameters["spread"], parameters["difference"], confidence, POWER)
        detect = max(detect, floor)
        explanation = (
            f"Below {floor} scores the spread is too poorly known for a t-test; "
            f"{detect} times would see a mean {parameters['difference']:g} of the range from "
            f"the threshold 80% of the time, with scores spreading "
            f"{parameters['spread']:g} of the range."
        )
        candidates = [
            Suggestion(times=floor, kind=SuggestionKind.floor,
                       label=f"{floor} · the least that means anything", default=False),
            Suggestion(times=detect, kind=SuggestionKind.detects_difference,
                       label=f"{detect} · sees a gap of {parameters['difference']:g}",
                       default=True),
            Suggestion(times=recommended, kind=SuggestionKind.recommended,
                       label=f"{recommended} · comfortable", default=False),
        ]
        # one suggestion per size, the default kept when sizes coincide
        by_times: dict[int, Suggestion] = {}
        for suggestion in candidates:
            if suggestion.times not in by_times or suggestion.default:
                by_times[suggestion.times] = suggestion
        return Sizing(floor, explanation, [by_times[times] for times in sorted(by_times)])
    raise ValueError(f"{entry.id} has no batch sizing")  # comparisons aren't sized here


def check_times(times: int, runs_per_time: int, floor: int, entry: CatalogueEntry,
                parameters: dict[str, float]) -> None:
    """422 when the times are below the floor or above what one batch may
    create."""
    problems = []
    if floor > MAX_TIMES:
        problems.append((("parameters",),
                         f"These parameters need at least {floor} times, more than the "
                         f"{MAX_TIMES} a batch can run: lower the target or the confidence"))
    elif times < floor:
        problems.append((("times",),
                         f"At least {floor} times for the {entry.name} with these parameters: "
                         f"below that no result could conclude anything"))
    elif times > MAX_TIMES:
        problems.append((("times",), f"At most {MAX_TIMES} times per batch"))
    elif times * runs_per_time > MAX_RUNS:
        problems.append((("times",),
                         f"{times} times × {runs_per_time} runs each is "
                         f"{times * runs_per_time} runs, more than the {MAX_RUNS} a batch can "
                         f"create: at most {MAX_RUNS // runs_per_time} times for this scope"))
    if problems:
        raise _invalid(problems)
