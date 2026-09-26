import logging
import math
from statistics import NormalDist
from statistics import mean as _mean
from statistics import stdev as _stdev

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import StatisticalVerificationModel
from assay.schemas import ZTestRequest, ZTestResult

logger = logging.getLogger(__name__)

# Shared standard normal distribution — used for CDF and inverse CDF lookups.
_nd = NormalDist()


async def run_z_test(req: ZTestRequest, session: AsyncSession) -> ZTestResult: # pragma: no cover
    """Run a one-sample z-test and persist the result.

    Tests whether the true mean of `scores` differs from `threshold`.
    Uses sample std as a population estimate — reliable for n >= 30.
    Orchestrates the pure math helpers below and delegates persistence to `_persist_verification`.
    """
    n, mu, sigma = _get_descriptive_statistics(req)

    z = _calculate_z_statistic(req, n, mu, sigma)

    p_value = _calculate_p_value(req, z)

    se = _calculate_se(n, sigma)
    z_crit = _calculate_critical_value(req)
    ci = _calculate_confidence_interval(mu, z_crit, se)

    result = _build_result(req, n, mu, sigma, z, p_value, ci)

    await _persist_verification(result, req, session)

    logger.info(
        "Recorded z-test verification: n=%d passed=%s p_value=%s",
        result.n, result.passed, result.p_value,
        extra={"n": result.n, "passed": result.passed, "p_value": result.p_value},
    )
    return result


def _build_result(
    req: ZTestRequest,
    n: int,
    mu: float,
    sigma: float,
    z: float,
    p_value: float,
    ci: tuple[float, float],
) -> ZTestResult:
    """Build the ZTestResult from computed values. Pure function — fully unit-testable."""
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


async def _persist_verification(
        result: ZTestResult,
        req: ZTestRequest,
        session: AsyncSession
) -> None:
    """Persist the z-test result as a StatisticalVerificationModel row.

    Separated from run_z_test to allow independent unit testing via a mocked session.
    """
    session.add(
        StatisticalVerificationModel(
            metric="z-test",
            test_type="z-test",
            threshold=req.threshold,
            alpha=req.alpha,
            alternative=req.alternative,
            z_statistic=result.z_statistic,
            p_value=result.p_value,
            passed=result.passed,
            result_detail=result.model_dump(),
        )
    )
    await session.commit()


def _get_descriptive_statistics(req: ZTestRequest) -> tuple[int, float, float]:
    n = len(req.scores)
    mu = _mean(req.scores)
    sigma = _stdev(req.scores)  # Bessel-corrected sample std (n-1 denominator)

    return n, mu, sigma


def _calculate_z_statistic(req: ZTestRequest, n: int, mu: float, sigma: float) -> float:
    if sigma == 0:
        # All scores identical — z is ±inf or 0; CDF handles these correctly.
        return math.inf if mu > req.threshold else (-math.inf if mu < req.threshold else 0.0)
    else:
        return (mu - req.threshold) / (sigma / math.sqrt(n))


def _calculate_p_value(req: ZTestRequest, z: float) -> float:
    # NormalDist.cdf handles ±inf via math.erf, so no special-casing needed.
    match req.alternative:
        case "greater":
            return 1 - _nd.cdf(z)
        case "less":
            return _nd.cdf(z)
        case "two-sided":
            return 2 * (1 - _nd.cdf(abs(z)))
        

def _calculate_se(n: int, sigma: float) -> float:
    return (sigma / math.sqrt(n)) if sigma > 0 else 0.0


def _calculate_critical_value(req: ZTestRequest) -> float:
    # CI is always two-sided (alpha/2), regardless of the test's alternative hypothesis.
    return _nd.inv_cdf(1 - req.alpha / 2)


def _calculate_confidence_interval(mu: float, z_crit: float, se: float) -> tuple[float, float]:
    return round(mu - z_crit * se, 6), round(mu + z_crit * se, 6)
