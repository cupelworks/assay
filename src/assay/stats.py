import math
from statistics import NormalDist
from statistics import mean as _mean
from statistics import stdev as _stdev

from assay.schemas.stats import ZTestRequest, ZTestResult

# Shared standard normal distribution — used for CDF and inverse CDF lookups.
_nd = NormalDist()


def run_z_test(req: ZTestRequest) -> ZTestResult:
    """One-sample z-test: tests whether the true mean of `scores` differs from `threshold`.

    Uses the sample standard deviation as an estimate of the population std.
    Reliable for n >= 30; for smaller samples a t-test would be more appropriate.
    """
    n = len(req.scores)
    mu = _mean(req.scores)
    sigma = _stdev(req.scores)  # Bessel-corrected sample std (n-1 denominator)

    if sigma == 0:
        # All scores identical — z is ±inf or 0; CDF handles these correctly.
        z = math.inf if mu > req.threshold else (-math.inf if mu < req.threshold else 0.0)
    else:
        z = (mu - req.threshold) / (sigma / math.sqrt(n))

    # NormalDist.cdf handles ±inf via math.erf, so no special-casing needed.
    match req.alternative:
        case "greater":
            p_value = 1 - _nd.cdf(z)
        case "less":
            p_value = _nd.cdf(z)
        case "two-sided":
            p_value = 2 * (1 - _nd.cdf(abs(z)))

    # Two-sided CI at (1 - alpha) confidence — useful regardless of the alternative chosen.
    se = (sigma / math.sqrt(n)) if sigma > 0 else 0.0
    z_crit = _nd.inv_cdf(1 - req.alpha / 2)
    ci = (round(mu - z_crit * se, 6), round(mu + z_crit * se, 6))

    return ZTestResult(
        n=n,
        mean=round(mu, 6),
        std=round(sigma, 6),
        threshold=req.threshold,
        alternative=req.alternative,
        z_statistic=round(z, 6) if math.isfinite(z) else z,
        p_value=round(p_value, 6),
        alpha=req.alpha,
        passed=p_value < req.alpha,
        confidence_interval=ci,
    )
