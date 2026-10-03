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


# ── an average, until there's an answer ──────────────────────────────────────
#
# The one-sample t-test at each wave's end, at one stricter level. A score's
# spread isn't known in advance, so the level can't be calibrated exactly as
# the pass/fail test's is: it's Pocock's constant for the batch's own looks —
# the boundary c such that a mean exactly at the threshold crosses it at some
# look with probability 1 − confidence, for normal scores — found by
# integrating the normal score process across the looks on a grid. The test
# at each look is then Student's t at that level, as the fixed test is:
# exact for normal scores in the limit, approximate for a few.

_GRID_STEP = 0.05
_GRID_LOW = -8.0


def _phi(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def _crossing(looks: tuple[int, ...], c: float, drift: float = 0.0) -> tuple[list, list]:
    """For the standardized mean Z_k = √n_k (mean_k − threshold) / σ at each
    look, with a true standardized gap `drift` per run: the chance of first
    crossing above c, and below −c, at each look. Recursive integration on a
    grid of z (Armitage–McPherson–Rowe)."""
    grid = [_GRID_LOW + i * _GRID_STEP for i in range(int((2 * -_GRID_LOW) / _GRID_STEP) + 1)]
    inside = [z for z in grid if -c < z < c]
    up, down = [], []
    density = None  # over `inside`, the undecided paths after the previous look
    previous = 0
    for look in looks:
        if density is None:
            mean = math.sqrt(look) * drift
            up.append(stats_math.normal_sf(c - mean))
            down.append(stats_math.normal_sf(c + mean))
            density = [_phi(z - mean) * _GRID_STEP for z in inside]
        else:
            a = math.sqrt(previous / look)
            b = (look - previous) * drift / math.sqrt(look)
            spread = math.sqrt((look - previous) / look)
            crossed_up = crossed_down = 0.0
            following = [0.0] * len(inside)
            for z, mass in zip(inside, density, strict=True):
                if mass < 1e-14:
                    continue
                centre = a * z + b
                crossed_up += mass * stats_math.normal_sf((c - centre) / spread)
                crossed_down += mass * stats_math.normal_sf((c + centre) / spread)
                for j, target in enumerate(inside):
                    following[j] += mass * _phi((target - centre) / spread) / spread * _GRID_STEP
            up.append(crossed_up)
            down.append(crossed_down)
            density = following
        previous = look
    return up, down


@lru_cache(maxsize=1024)
def mean_level(looks: tuple[int, ...], confidence: float) -> float:
    """Each look's confidence for the average: Φ(c), c Pocock's constant for
    these looks — the chance of a wrong pass over all the looks, for a mean
    exactly at the threshold, is 1 − confidence (and of a wrong fail, by
    symmetry)."""
    alpha = 1 - confidence
    if len(looks) == 1:
        return confidence
    low, high = stats_math.z_quantile(confidence), stats_math.z_quantile(1 - alpha / len(looks))
    for _ in range(25):
        middle = (low + high) / 2
        if sum(_crossing(looks, middle)[0]) > alpha:
            low = middle
        else:
            high = middle
    return 1 - stats_math.normal_sf(high)


@lru_cache(maxsize=1024)
def mean_wave_plan(maximum: int, floor: int, confidence: float) -> WavePlan:
    """The looks for an average: the first at the t-test's floor (too few
    scores mean nothing), then evenly spaced up to the maximum."""
    looks = _looks(floor, maximum)
    return WavePlan(looks=looks, level=mean_level(looks, confidence))


def decide_mean(scores: list[tuple[int, float]], plan: WavePlan, threshold: float,
                higher_is_better: bool, through_look: int | None = None) -> tuple[
                    str | None, int | None, stats_math.OneSampleTResult | None]:
    """An average's sequential verdict from its scores so far ((time,
    score)): the t-test at the plan's level at each look reached, on the
    scores up to it; the first that decides is the answer. Returns the
    verdict ("pass", "fail" or None), the look, and the test there."""
    looks = plan.looks if through_look is None else plan.looks[:through_look]
    ordered = sorted(scores)
    last = None
    for look in looks:
        values = [score for time, score in ordered if time <= look]
        if len(values) < 2:
            continue
        test = stats_math.one_sample_t(values, threshold, higher_is_better, plan.level)
        last = test
        if test.verdict == stats_math.Verdict.passed:
            return "pass", look, test
        if test.verdict == stats_math.Verdict.failed:
            return "fail", look, test
    return None, None, last


def mean_look_chances(plan: WavePlan, mean: float, sd: float, history_runs: int,
                      threshold: float, higher_is_better: bool) -> LookChances:
    """The chance an average is decided by each look, averaged over what its
    true mean could be given its past scores (normal around their average,
    with their standard error; the spread taken as known). A spread of 0 is
    certain at the first look, undecidable exactly on the threshold."""
    sign = 1.0 if higher_is_better else -1.0
    looks = plan.looks
    if sd <= 0:
        gap = sign * (mean - threshold)
        passes = 1.0 if gap > 0 else 0.0
        fails = 1.0 if gap < 0 else 0.0
        return LookChances(looks=looks, passed_by=tuple(passes for _ in looks),
                           failed_by=tuple(fails for _ in looks))
    c = stats_math.z_quantile(plan.level)
    centre = sign * (mean - threshold) / sd           # the standardized gap per run
    uncertainty = 1 / math.sqrt(max(history_runs, 1))  # its standard error
    passed = [0.0] * len(looks)
    failed = [0.0] * len(looks)
    for node, weight in _GAUSS_HERMITE:
        drift = centre + math.sqrt(2) * uncertainty * node
        up, down = _crossing(looks, c, drift)
        for k in range(len(looks)):
            passed[k] += weight * sum(up[:k + 1])
            failed[k] += weight * sum(down[:k + 1])
    total = sum(w for _, w in _GAUSS_HERMITE)
    return LookChances(looks=looks, passed_by=tuple(min(1.0, p / total) for p in passed),
                       failed_by=tuple(min(1.0, f / total) for f in failed))


# Gauss–Hermite nodes and weights (9 points), for averaging over a normal
_GAUSS_HERMITE = (
    (-3.190993201781528, 3.960697726326438e-05), (-2.266580584531843, 0.004943624275536947),
    (-1.468553289216668, 0.08847452739437657), (-0.7235510187528376, 0.4326515590025558),
    (0.0, 0.7202352156060510), (0.7235510187528376, 0.4326515590025558),
    (1.468553289216668, 0.08847452739437657), (2.266580584531843, 0.004943624275536947),
    (3.190993201781528, 3.960697726326438e-05),
)
