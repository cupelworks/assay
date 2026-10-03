"""Run until there's an answer (docs/statistics/dev_notes.md notes 30–32): a
batch that runs in waves and stops as soon as every check has one.

The test is the fixed gate's, applied at each wave's end ("a look") at a
stricter level, so its claims are word for word the fixed test's: a pass
means "passes at least the target", a fail "passes less than the target",
each wrong at most 1 − confidence of the time overall. The stricter level is
calibrated exactly for the batch's own looks: the same level at every look,
the least strict one whose chance of a wrong answer across all the looks —
computed exactly with the binomial, when the check passes exactly the target
— stays within the stated error (a Pocock-style boundary, found by
bisection). A perfect record therefore stops at the first look, a little
later than the fixed test's floor.

Both the API (the estimate, the result) and the worker (deciding after each
wave whether to run the next) use this module, so it lives outside the
API-only code.
"""
import math
from dataclasses import dataclass
from functools import lru_cache

from assay import stats_math

# the looks a batch plans: its first at the strict floor, the last at the maximum
LOOKS = 8


@dataclass(frozen=True)
class WavePlan:
    """When a batch looks, and how strictly. `looks` are cumulative times
    (the first wave runs 1..looks[0], the next looks[0]+1..looks[1], …);
    `level` is each look's confidence."""
    looks: tuple[int, ...]
    level: float

    @property
    def first(self) -> int:
        return self.looks[0]

    @property
    def maximum(self) -> int:
        return self.looks[-1]

    @property
    def wave_size(self) -> int:
        """The size of the waves after the first (the last may be smaller)."""
        return self.looks[1] - self.looks[0] if len(self.looks) > 1 else self.looks[0]

    def to_json(self) -> dict:
        return {"looks": list(self.looks), "level": self.level}

    @classmethod
    def from_json(cls, data: dict) -> "WavePlan":
        return cls(looks=tuple(data["looks"]), level=data["level"])


def _looks(first: int, maximum: int) -> tuple[int, ...]:
    """The first look, then evenly spaced up to the maximum, at most LOOKS."""
    if maximum <= first:
        return (first,)
    count = min(LOOKS, maximum - first + 1)
    step = (maximum - first) / (count - 1) if count > 1 else 0
    return tuple(sorted({first + round(step * i) for i in range(count)} | {maximum}))


@lru_cache(maxsize=1024)
def wave_plan(maximum: int, target: float, confidence: float) -> WavePlan:
    """The looks and their level: the level calibrated for the looks, the
    first look at the floor that level allows (a perfect record can't pass
    before it), and the level calibrated again for the final looks."""
    level = 1 - (1 - confidence) / LOOKS  # the even split: valid, a first guess
    looks = _looks(stats_math.binomial_floor(target, level), maximum)
    for _ in range(3):
        level = _calibrate(looks, target, confidence)
        moved = _looks(stats_math.binomial_floor(target, level), maximum)
        if moved == looks:
            break
        looks = moved
    return WavePlan(looks=looks, level=_calibrate(looks, target, confidence))


@lru_cache(maxsize=1024)
def _calibrate(looks: tuple[int, ...], target: float, confidence: float) -> float:
    """The least strict level, the same at every look, whose chance of a wrong
    answer over all the looks stays within 1 − confidence each way, when the
    check passes exactly the target (where both errors are largest). Found by
    bisection on the error; never stricter than the even split, never looser
    than the fixed test."""
    alpha = 1 - confidence
    low, high = alpha / len(looks), alpha  # per-look errors: valid, too loose
    for _ in range(12):  # the level to within 0.05%: finer changes no boundary
        middle = math.sqrt(low * high)
        passes, fails = _errors(looks, target, 1 - middle)
        if passes <= alpha and fails <= alpha:
            low = middle
        else:
            high = middle
    return 1 - low


def _errors(looks: tuple[int, ...], target: float, level: float) -> tuple[float, float]:
    """The exact chance of ever passing and of ever failing, over the looks,
    for a check that passes exactly `target` of the time."""
    alive = {0: 1.0}
    previous = 0
    passed = failed = 0.0
    for look in looks:
        wave = look - previous
        pmf = [stats_math.binomial_pmf(k, wave, target) for k in range(wave + 1)]
        reached: dict[int, float] = {}
        for passes, chance in alive.items():
            if chance < 1e-15:
                continue
            for extra, p in enumerate(pmf):
                reached[passes + extra] = reached.get(passes + extra, 0.0) + chance * p
        rule = stats_math.binomial_gate_rule(look, target, level)
        alive = {}
        for passes, chance in reached.items():
            if rule.pass_at_least is not None and passes >= rule.pass_at_least:
                passed += chance
            elif rule.fail_at_most is not None and passes <= rule.fail_at_most:
                failed += chance
            else:
                alive[passes] = chance
        previous = look
    return passed, failed


@dataclass(frozen=True)
class SequentialVerdict:
    """What a check's runs so far decided: `verdict` "pass", "fail" or None
    (undecided), the look it was decided at, and its counts then."""
    verdict: str | None
    at_time: int | None
    passes: int
    evaluated: int


def decide(outcomes: list[tuple[int, bool]], plan: WavePlan, target: float,
           through_look: int | None = None, agreement: bool = False) -> SequentialVerdict:
    """A check's sequential verdict from its results so far: `outcomes` are
    (time, passed) for every run that gave it a result. At each look reached
    (all of them, or the first `through_look`), the gate at the strict level
    on the results up to that time; the first look that decides is the
    answer, and it never changes. `agreement`: judge stability's — the runs
    that gave the usual verdict count as passes."""
    looks = plan.looks if through_look is None else plan.looks[:through_look]
    ordered = sorted(outcomes)
    passes = evaluated = 0
    index = 0
    for look in looks:
        while index < len(ordered) and ordered[index][0] <= look:
            passes += ordered[index][1]
            evaluated += 1
            index += 1
        if evaluated == 0:
            continue
        counted = max(passes, evaluated - passes) if agreement else passes
        rule = stats_math.binomial_gate_rule(evaluated, target, plan.level)
        if rule.pass_at_least is not None and counted >= rule.pass_at_least:
            return SequentialVerdict("pass", look, counted, evaluated)
        if rule.fail_at_most is not None and counted <= rule.fail_at_most:
            return SequentialVerdict("fail", look, counted, evaluated)
    counted = max(passes, evaluated - passes) if agreement else passes
    return SequentialVerdict(None, None, counted, evaluated)


# ── the odds, before running ─────────────────────────────────────────────────


@dataclass(frozen=True)
class LookChances:
    """For one check, the chance it's been decided by each look — passed and
    failed separately, cumulative."""
    looks: tuple[int, ...]
    passed_by: tuple[float, ...]
    failed_by: tuple[float, ...]

    def decided_by(self, k: int) -> float:
        return self.passed_by[k] + self.failed_by[k]


def look_chances(plan: WavePlan, target: float, passed: int, failed: int) -> LookChances:
    """The chance a check is decided by each look, averaged over what its rate
    could be given its history — exactly: the runs of a check whose rate is
    Beta(passed + 1, failed + 1) are a Pólya urn, so between looks the passes
    of the next wave follow the beta-binomial updated by the passes so far.
    Every run is assumed to give the check a result."""
    a0, b0 = passed + 1.0, failed + 1.0
    alive = {0: 1.0}  # passes so far → chance of being here, undecided
    previous = 0
    passed_by, failed_by = [], []
    done_pass = done_fail = 0.0
    for look in plan.looks:
        wave = look - previous
        reached: dict[int, float] = {}
        for passes, chance in alive.items():
            if chance < 1e-15:
                continue
            for extra, p in enumerate(_beta_binomial(wave, a0 + passes,
                                                     b0 + previous - passes)):
                if p:
                    reached[passes + extra] = reached.get(passes + extra, 0.0) + chance * p
        rule = stats_math.binomial_gate_rule(look, target, plan.level)
        alive = {}
        for passes, chance in reached.items():
            if rule.pass_at_least is not None and passes >= rule.pass_at_least:
                done_pass += chance
            elif rule.fail_at_most is not None and passes <= rule.fail_at_most:
                done_fail += chance
            else:
                alive[passes] = chance
        passed_by.append(min(1.0, done_pass))
        failed_by.append(min(1.0, done_fail))
        previous = look
    return LookChances(looks=plan.looks, passed_by=tuple(passed_by), failed_by=tuple(failed_by))


def _beta_binomial(n: int, a: float, b: float) -> list[float]:
    return stats_math.beta_binomial_pmf(n, a, b)
