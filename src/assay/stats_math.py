"""The arithmetic behind "run with statistics": pure functions on the
standard library, no numpy or scipy (docs/statistics/dev_notes.md note 9 —
scipy would add some 30 MB plus NumPy to an API image of about 48 MB, for
functions this short).

Every function here works on plain numbers and returns plain numbers or small
frozen dataclasses; nothing reads the database or knows about runs. The
statistics services fold runs into the counts and score lists these take,
and round to four decimals only when they build a response — the maths keeps
full precision.

Conventions:
- `confidence` is the confidence level (0.95), `alpha` is 1 - confidence.
- Verdicts are three-way (note 7): `pass` when the data prove the claim at
  the confidence level, `fail` when they prove the opposite, `inconclusive`
  otherwise. A one-sided test is run in each direction; at most one of the
  two can reject at a confidence above 50%.
- Every interval a verdict is drawn from comes from the same arithmetic as
  the verdict, so a displayed bound and a verdict never disagree: the gate's
  exact one-sided bounds, the t-test's one-sided t bounds, the comparison's
  Newcombe interval.
"""
import math
from dataclasses import dataclass
from enum import StrEnum
from statistics import NormalDist

_STANDARD_NORMAL = NormalDist()
# Bisection runs until the bracket is narrower than this: far below the four
# decimals anything is reported with.
_TOLERANCE = 1e-12
_MAX_ITERATIONS = 300


class Verdict(StrEnum):
    passed = "pass"
    failed = "fail"
    inconclusive = "inconclusive"


class ComparisonVerdict(StrEnum):
    better = "better"
    worse = "worse"
    no_difference = "no_difference"


# ── the normal distribution ──────────────────────────────────────────────────


def z_quantile(probability: float) -> float:
    """The standard normal quantile: z_quantile(0.975) = 1.95996..."""
    return _STANDARD_NORMAL.inv_cdf(probability)


def normal_sf(z: float) -> float:
    """P(Z >= z) for a standard normal Z, accurate in the far tail."""
    return 0.5 * math.erfc(z / math.sqrt(2))


# ── the binomial distribution ────────────────────────────────────────────────


def _log_binomial_pmf(k: int, n: int, p: float) -> float:
    if p == 0.0:
        return 0.0 if k == 0 else -math.inf
    if p == 1.0:
        return 0.0 if k == n else -math.inf
    return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
            + k * math.log(p) + (n - k) * math.log1p(-p))


def binomial_pmf(k: int, n: int, p: float) -> float:
    """P(X = k) for X ~ Binomial(n, p)."""
    if k < 0 or k > n:
        return 0.0
    return math.exp(_log_binomial_pmf(k, n, p))


def binomial_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p): the chance of at least k passes."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return min(1.0, math.fsum(binomial_pmf(i, n, p) for i in range(k, n + 1)))


def binomial_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p): the chance of at most k passes."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return min(1.0, math.fsum(binomial_pmf(i, n, p) for i in range(0, k + 1)))


def _bisect(function, low: float, high: float, target: float, increasing: bool) -> float:
    """The x in [low, high] where a monotone function crosses target."""
    for _ in range(_MAX_ITERATIONS):
        middle = (low + high) / 2
        value = function(middle)
        if (value < target) == increasing:
            low = middle
        else:
            high = middle
        if high - low < _TOLERANCE:
            break
    return (low + high) / 2


def exact_lower_bound(passes: int, n: int, confidence: float) -> float:
    """The exact (Clopper–Pearson) one-sided lower confidence bound of a pass
    rate: the smallest rate that could still have produced at least `passes`
    passes in n runs with probability more than alpha.

    29 passes of 29 at 95% → 0.9019: "95% confident the rate is at least
    90.2%". 0 passes → 0.
    """
    _check_counts(passes, n)
    alpha = 1 - confidence
    if passes == 0:
        return 0.0
    if passes == n:
        return alpha ** (1 / n)
    # P(X >= passes | p) increases with p; find where it equals alpha
    return _bisect(lambda p: binomial_sf(passes, n, p), 0.0, 1.0, alpha, increasing=True)


def exact_upper_bound(passes: int, n: int, confidence: float) -> float:
    """The exact (Clopper–Pearson) one-sided upper confidence bound of a pass
    rate. n passes of n → 1."""
    _check_counts(passes, n)
    alpha = 1 - confidence
    if passes == n:
        return 1.0
    if passes == 0:
        return 1 - alpha ** (1 / n)
    # P(X <= passes | p) decreases with p; find where it equals alpha
    return _bisect(lambda p: binomial_cdf(passes, n, p), 0.0, 1.0, alpha, increasing=False)


def wilson_interval(passes: int, n: int, confidence: float) -> tuple[float, float]:
    """The two-sided Wilson score interval of a pass rate: the descriptive
    "give or take" of a rate with no verdict attached. 18 of 20 at 95% →
    (0.699, 0.972)."""
    _check_counts(passes, n)
    z = z_quantile(1 - (1 - confidence) / 2)
    rate = passes / n
    denominator = 1 + z * z / n
    centre = (rate + z * z / (2 * n)) / denominator
    half_width = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half_width), min(1.0, centre + half_width)


def _check_counts(passes: int, n: int) -> None:
    if n <= 0:
        raise ValueError("A rate needs at least one observation")
    if passes < 0 or passes > n:
        raise ValueError("Passes must be between 0 and n")


# ── the binomial gate ────────────────────────────────────────────────────────


def binomial_floor(target: float, confidence: float) -> int:
    """The fewest runs at which a binomial gate can pass at all — every run
    passing: the smallest n with target**n <= alpha, i.e.
    n >= ln(alpha) / ln(target). 14 at 80%, 29 at 90%, 59 at 95%, 299 at 99%
    (95% confidence). Below it, no result can prove the target."""
    _check_target(target)
    alpha = 1 - confidence
    n = max(1, math.ceil(math.log(alpha) / math.log(target) - 1e-9))
    while target ** n > alpha:  # guard against the logarithms' rounding
        n += 1
    while n > 1 and target ** (n - 1) <= alpha:
        n -= 1
    return n


def binomial_runs_allowing(misses: int, target: float, confidence: float) -> int:
    """The fewest runs at which the gate passes even with `misses` failures:
    46 at 90% allows one miss (95% confidence). misses=0 is the floor."""
    _check_target(target)
    if misses < 0:
        raise ValueError("Misses can't be negative")
    alpha = 1 - confidence
    n = binomial_floor(target, confidence) + misses
    while binomial_sf(n - misses, n, target) > alpha:
        n += 1
    return n


@dataclass(frozen=True)
class GateRule:
    """What a gate at n runs decides, before running: it passes with at least
    `pass_at_least` passes, fails with at most `fail_at_most`, and is
    inconclusive in between. Either is None when no count can decide that way
    at this n (pass_at_least is None below the floor)."""
    n: int
    pass_at_least: int | None
    fail_at_most: int | None


def binomial_gate_rule(n: int, target: float, confidence: float) -> GateRule:
    _check_target(target)
    if n <= 0:
        return GateRule(n=n, pass_at_least=None, fail_at_most=None)
    alpha = 1 - confidence
    pass_at_least = next(
        (k for k in range(0, n + 1) if binomial_sf(k, n, target) <= alpha), None)
    fail_at_most = next(
        (k for k in range(n, -1, -1) if binomial_cdf(k, n, target) <= alpha), None)
    return GateRule(n=n, pass_at_least=pass_at_least, fail_at_most=fail_at_most)


@dataclass(frozen=True)
class GateResult:
    """A binomial gate on one check: `passes` of `n` evaluated runs against a
    target pass rate."""
    passes: int
    n: int
    target: float
    confidence: float
    verdict: Verdict
    # P(at least this many passes | the rate is exactly the target): small
    # means the rate is above the target
    p_value_pass: float
    # P(at most this many passes | the rate is exactly the target): small
    # means the rate is below the target
    p_value_fail: float
    lower: float  # exact one-sided lower bound at the confidence level
    upper: float  # exact one-sided upper bound at the confidence level
    rule: GateRule


def binomial_gate(passes: int, n: int, target: float, confidence: float) -> GateResult:
    """Does the check pass at least `target` of the time? An exact binomial
    test each way: pass when P(X >= passes | target) <= alpha — equivalently
    the exact lower bound is at least the target; fail when
    P(X <= passes | target) <= alpha — the exact upper bound is below it;
    inconclusive otherwise. 29 of 29 against 90% passes (p = 0.047); 18 of 20
    and 20 of 20 are inconclusive."""
    _check_target(target)
    _check_counts(passes, n)
    alpha = 1 - confidence
    p_pass = binomial_sf(passes, n, target)
    p_fail = binomial_cdf(passes, n, target)
    if p_pass <= alpha:
        verdict = Verdict.passed
    elif p_fail <= alpha:
        verdict = Verdict.failed
    else:
        verdict = Verdict.inconclusive
    return GateResult(
        passes=passes, n=n, target=target, confidence=confidence, verdict=verdict,
        p_value_pass=p_pass, p_value_fail=p_fail,
        lower=exact_lower_bound(passes, n, confidence),
        upper=exact_upper_bound(passes, n, confidence),
        rule=binomial_gate_rule(n, target, confidence),
    )


def binomial_gate_runs_to_decide(passes: int, n: int, target: float,
                                 confidence: float, limit: int = 100_000) -> int | None:
    """After an inconclusive gate: how big a *new* batch would likely decide
    it, if the check keeps passing at the rate this one observed.

    The smallest N at which a batch passing at the observed rate (round(rate
    × N) passes, the most likely count) would prove the side the rate is on:
    above the target → pass, below → fail. 27 of 29 against 90% → about 239;
    28 of 29 → 61. A new batch, never an extension: pooling batches until one
    passes would make the stated confidence untrue (note 18). None when the
    observed rate is exactly the target (no batch size would decide it), or
    nothing was observed, or it would take more than `limit` runs.
    """
    _check_target(target)
    if n <= 0:
        return None
    rate = passes / n
    if rate == target:
        return None
    alpha = 1 - confidence
    above = rate > target
    candidate = 1
    while candidate <= limit:
        expected = round(rate * candidate)
        if above and binomial_sf(expected, candidate, target) <= alpha:
            break
        if not above and binomial_cdf(expected, candidate, target) <= alpha:
            break
        candidate += 1
    else:
        return None
    return candidate


def _check_target(target: float) -> None:
    if not 0 < target < 1:
        raise ValueError("A target pass rate must be strictly between 0 and 1")


# ── the t-distribution ───────────────────────────────────────────────────────


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """The continued fraction of the incomplete beta function (Numerical
    Recipes' betacf, modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1, a - 1
    c = 1.0
    d = 1 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1 / d
    h = d
    for m in range(1, _MAX_ITERATIONS + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return h


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """I_x(a, b), the regularised incomplete beta function."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    log_front = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                 + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(log_front)
    if x < (a + 1) / (a + b + 2):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1 - front * _beta_continued_fraction(b, a, 1 - x) / b


def t_cdf(t: float, df: float) -> float:
    """P(T <= t) for Student's t with df degrees of freedom."""
    if df <= 0:
        raise ValueError("Degrees of freedom must be positive")
    if math.isinf(t):
        return 1.0 if t > 0 else 0.0
    tail = 0.5 * regularized_incomplete_beta(df / 2, 0.5, df / (df + t * t))
    return 1 - tail if t > 0 else tail


def t_sf(t: float, df: float) -> float:
    """P(T >= t), computed without the cancellation of 1 - t_cdf in the tail."""
    if math.isinf(t):
        return 0.0 if t > 0 else 1.0
    tail = 0.5 * regularized_incomplete_beta(df / 2, 0.5, df / (df + t * t))
    return tail if t > 0 else 1 - tail


def t_quantile(probability: float, df: float) -> float:
    """The t such that P(T <= t) = probability. t(0.975, 19) = 2.093;
    t(0.975, 1) = 12.706; t(0.975, 1000) = 1.962."""
    if not 0 < probability < 1:
        raise ValueError("A quantile's probability must be strictly between 0 and 1")
    if probability == 0.5:
        return 0.0
    if probability < 0.5:
        return -t_quantile(1 - probability, df)
    high = 1.0
    while t_cdf(high, df) < probability:
        high *= 2
    return _bisect(lambda t: t_cdf(t, df), 0.0, high, probability, increasing=True)


# ── describing a sample of scores ───────────────────────────────────────────


@dataclass(frozen=True)
class ScoreSummary:
    """A sample of scores described: everything a box plot or an error bar
    needs. sd is the sample standard deviation (n - 1); None with one score."""
    n: int
    mean: float
    sd: float | None
    minimum: float
    maximum: float
    p10: float
    p25: float
    median: float
    p75: float
    p90: float


def quantile(sorted_values: list[float], probability: float) -> float:
    """The linear-interpolation quantile of sorted values (the default of
    NumPy and R's type 7): between the two order statistics around
    (n - 1) × probability."""
    if not sorted_values:
        raise ValueError("A quantile needs at least one value")
    position = (len(sorted_values) - 1) * probability
    below = math.floor(position)
    above = min(below + 1, len(sorted_values) - 1)
    weight = position - below
    return sorted_values[below] + (sorted_values[above] - sorted_values[below]) * weight


def summarize_scores(values: list[float]) -> ScoreSummary:
    if not values:
        raise ValueError("A summary needs at least one score")
    ordered = sorted(values)
    n = len(ordered)
    mean = math.fsum(ordered) / n
    sd = (math.sqrt(math.fsum((v - mean) ** 2 for v in ordered) / (n - 1))
          if n > 1 else None)
    return ScoreSummary(
        n=n, mean=mean, sd=sd, minimum=ordered[0], maximum=ordered[-1],
        p10=quantile(ordered, 0.10), p25=quantile(ordered, 0.25),
        median=quantile(ordered, 0.5), p75=quantile(ordered, 0.75),
        p90=quantile(ordered, 0.90),
    )


# ── the one-sample t-test ────────────────────────────────────────────────────


@dataclass(frozen=True)
class OneSampleTResult:
    """Is the mean score on the right side of the threshold?

    `higher_is_better` is the type's comparison: gte (higher passes) → True,
    lte → False. The verdict is pass when the data prove the mean is on the
    passing side (mean >= threshold for gte), fail when they prove it's on
    the failing side, inconclusive otherwise. lower and upper are one-sided
    bounds at the confidence level — the verdict's own arithmetic: for gte,
    pass ⟺ lower >= threshold and fail ⟺ upper < threshold.
    """
    n: int
    mean: float
    sd: float | None
    standard_error: float | None
    df: int | None
    t: float | None
    threshold: float
    higher_is_better: bool
    confidence: float
    verdict: Verdict
    p_value_pass: float | None
    p_value_fail: float | None
    lower: float | None
    upper: float | None


def one_sample_t(values: list[float], threshold: float, higher_is_better: bool,
                 confidence: float) -> OneSampleTResult:
    """The one-sample t-test of the mean score against a threshold, each way
    (the old z-test's job done right: valid for small samples).

    One score proves nothing: inconclusive, no bounds. Scores that are all
    equal (a recorded answer scored by a metric gives the same score every
    run) have no spread: the mean is then known exactly, and the verdict is
    the threshold comparison itself, pass or fail, with both bounds at the
    mean.
    """
    n = len(values)
    if n == 0:
        raise ValueError("A t-test needs at least one score")
    mean = math.fsum(values) / n
    if n == 1:
        return OneSampleTResult(
            n=1, mean=mean, sd=None, standard_error=None, df=None, t=None,
            threshold=threshold, higher_is_better=higher_is_better, confidence=confidence,
            verdict=Verdict.inconclusive, p_value_pass=None, p_value_fail=None,
            lower=None, upper=None,
        )
    sd = math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (n - 1))
    se = sd / math.sqrt(n)
    df = n - 1
    if se == 0:
        on_passing_side = mean >= threshold if higher_is_better else mean <= threshold
        return OneSampleTResult(
            n=n, mean=mean, sd=0.0, standard_error=0.0, df=df, t=None,
            threshold=threshold, higher_is_better=higher_is_better, confidence=confidence,
            verdict=Verdict.passed if on_passing_side else Verdict.failed,
            p_value_pass=0.0 if on_passing_side else 1.0,
            p_value_fail=1.0 if on_passing_side else 0.0,
            lower=mean, upper=mean,
        )
    alpha = 1 - confidence
    t = (mean - threshold) / se
    critical = t_quantile(confidence, df)
    lower, upper = mean - critical * se, mean + critical * se
    # P(a mean this high or higher | the true mean is the threshold)
    p_above = t_sf(t, df)
    p_below = t_cdf(t, df)
    p_pass, p_fail = (p_above, p_below) if higher_is_better else (p_below, p_above)
    if p_pass <= alpha:
        verdict = Verdict.passed
    elif p_fail <= alpha:
        verdict = Verdict.failed
    else:
        verdict = Verdict.inconclusive
    return OneSampleTResult(
        n=n, mean=mean, sd=sd, standard_error=se, df=df, t=t, threshold=threshold,
        higher_is_better=higher_is_better, confidence=confidence, verdict=verdict,
        p_value_pass=p_pass, p_value_fail=p_fail, lower=lower, upper=upper,
    )


def runs_needed_for_mean(spread: float, difference: float, confidence: float,
                         power: float = 0.8) -> int:
    """How many runs a one-sided one-sample t-test needs to detect a mean
    `difference` away from the threshold when scores spread with standard
    deviation `spread`, with the given power: n = ((t_{1-α} + t_power) · σ/δ)²,
    iterated with the t quantiles at n - 1 degrees of freedom until it
    settles (the z version is the first guess). σ = 0.1, δ = 0.05 → 27,
    as G*Power gives for an effect size of 0.5."""
    if spread <= 0 or difference <= 0:
        raise ValueError("Spread and difference must be positive")
    ratio = spread / difference
    n = max(2, math.ceil((z_quantile(confidence) + z_quantile(power)) ** 2 * ratio ** 2))
    for _ in range(100):
        df = n - 1
        needed = max(2, math.ceil(
            (t_quantile(confidence, df) + t_quantile(power, df)) ** 2 * ratio ** 2))
        if needed == n:
            break
        n = needed
    return n


def one_sample_t_runs_to_decide(mean: float, sd: float, threshold: float,
                                confidence: float, power: float = 0.8,
                                limit: int = 100_000) -> int | None:
    """After an inconclusive t-test: how big a new batch would likely decide
    it, from the observed spread and gap to the threshold. None when the gap
    or the spread is zero, or it would take more than `limit` runs."""
    gap = abs(mean - threshold)
    if gap == 0 or sd <= 0:
        return None
    needed = runs_needed_for_mean(sd, gap, confidence, power)
    return needed if needed <= limit else None


# ── comparing two pass rates ─────────────────────────────────────────────────


@dataclass(frozen=True)
class TwoByTwo:
    """Pearson's chi-square on a 2×2 table (no continuity correction) and
    Fisher's exact test, with the smallest expected count that decides which
    is the right p-value to show (the cell rule: every expected count at
    least 5 for chi-square)."""
    chi_square: float
    p_value_chi_square: float
    p_value_fisher: float
    min_expected: float


def chi_square_2x2(a: int, b: int, c: int, d: int) -> tuple[float, float, float]:
    """(chi-square, p-value, smallest expected count) for the table
    [[a, b], [c, d]] — rows the two batches, columns passed / failed. With
    one degree of freedom the p-value is erfc(√(χ²/2)). A table with an empty
    row or column has no association to test: χ² = 0, p = 1."""
    n = a + b + c + d
    rows, columns = (a + b, c + d), (a + c, b + d)
    if n == 0 or 0 in rows or 0 in columns:
        expected = [r * k / n for r in rows for k in columns] if n else [0.0]
        return 0.0, 1.0, min(expected)
    expected = [rows[0] * columns[0] / n, rows[0] * columns[1] / n,
                rows[1] * columns[0] / n, rows[1] * columns[1] / n]
    observed = [a, b, c, d]
    chi = math.fsum((o - e) ** 2 / e for o, e in zip(observed, expected, strict=True))
    return chi, math.erfc(math.sqrt(chi / 2)), min(expected)


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Fisher's exact two-sided p-value for [[a, b], [c, d]]: the sum of the
    probabilities of every table with the same margins that is no likelier
    than the one observed. No minimum sample size."""
    row1, column1, n = a + b, a + c, a + b + c + d
    if n == 0:
        return 1.0
    low, high = max(0, column1 - (n - row1)), min(row1, column1)

    def log_probability(x: int) -> float:
        return (_log_comb(row1, x) + _log_comb(n - row1, column1 - x)
                - _log_comb(n, column1))

    observed = log_probability(a)
    total = math.fsum(
        math.exp(log_probability(x)) for x in range(low, high + 1)
        if log_probability(x) <= observed + 1e-7
    )
    return min(1.0, total)


def _log_comb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def two_by_two(a: int, b: int, c: int, d: int) -> TwoByTwo:
    chi, p_chi, min_expected = chi_square_2x2(a, b, c, d)
    return TwoByTwo(chi_square=chi, p_value_chi_square=p_chi,
                    p_value_fisher=fisher_exact(a, b, c, d), min_expected=min_expected)


def newcombe_interval(passes_a: int, n_a: int, passes_b: int, n_b: int,
                      confidence: float) -> tuple[float, float, float]:
    """The difference of two pass rates, B − A, with Newcombe's hybrid score
    interval (his method 10: built from the two Wilson intervals). Returns
    (lower, difference, upper), two-sided at the confidence level."""
    rate_a, rate_b = passes_a / n_a, passes_b / n_b
    low_a, high_a = wilson_interval(passes_a, n_a, confidence)
    low_b, high_b = wilson_interval(passes_b, n_b, confidence)
    difference = rate_b - rate_a
    lower = difference - math.sqrt((rate_b - low_b) ** 2 + (high_a - rate_a) ** 2)
    upper = difference + math.sqrt((high_b - rate_b) ** 2 + (rate_a - low_a) ** 2)
    return max(-1.0, lower), difference, min(1.0, upper)


@dataclass(frozen=True)
class ProportionComparison:
    """B against A on a pass rate. The verdict comes from the Newcombe
    interval of B − A alone (decided, note 18): better when it lies above 0,
    worse when below, no real difference at this sample size otherwise. The
    p-value is shown beside it, chi-square's when every expected count is at
    least 5, Fisher's exact otherwise — it informs, it never decides."""
    passes_a: int
    n_a: int
    passes_b: int
    n_b: int
    confidence: float
    lower: float
    difference: float
    upper: float
    verdict: ComparisonVerdict
    p_value: float
    p_value_method: str  # "chi_square" or "fisher_exact"
    min_expected: float


def compare_proportions(passes_a: int, n_a: int, passes_b: int, n_b: int,
                        confidence: float) -> ProportionComparison:
    _check_counts(passes_a, n_a)
    _check_counts(passes_b, n_b)
    lower, difference, upper = newcombe_interval(passes_a, n_a, passes_b, n_b, confidence)
    if lower > 0:
        verdict = ComparisonVerdict.better
    elif upper < 0:
        verdict = ComparisonVerdict.worse
    else:
        verdict = ComparisonVerdict.no_difference
    table = two_by_two(passes_a, n_a - passes_a, passes_b, n_b - passes_b)
    use_fisher = table.min_expected < 5
    return ProportionComparison(
        passes_a=passes_a, n_a=n_a, passes_b=passes_b, n_b=n_b, confidence=confidence,
        lower=lower, difference=difference, upper=upper, verdict=verdict,
        p_value=table.p_value_fisher if use_fisher else table.p_value_chi_square,
        p_value_method="fisher_exact" if use_fisher else "chi_square",
        min_expected=table.min_expected,
    )


def runs_needed_for_proportions(rate_a: float, rate_b: float, confidence: float,
                                power: float = 0.8) -> int | None:
    """Runs per batch needed to see the difference between two pass rates,
    two-sided, with the given power (the normal-approximation formula without
    continuity correction): 80% vs 90% at 95% confidence and 80% power →
    199 per batch. None when the rates are equal (no batch size shows a
    difference that isn't there)."""
    if rate_a == rate_b:
        return None
    alpha = 1 - confidence
    z_alpha, z_beta = z_quantile(1 - alpha / 2), z_quantile(power)
    pooled = (rate_a + rate_b) / 2
    numerator = (z_alpha * math.sqrt(2 * pooled * (1 - pooled))
                 + z_beta * math.sqrt(rate_a * (1 - rate_a) + rate_b * (1 - rate_b)))
    return max(2, math.ceil(numerator ** 2 / (rate_a - rate_b) ** 2))
