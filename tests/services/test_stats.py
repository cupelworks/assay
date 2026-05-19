import asyncio
import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from assay.models import StatisticalVerificationModel
from assay.schemas import ZTestResult
from assay.services.stats import (
    _build_result,
    _calculate_confidence_interval,
    _calculate_critical_value,
    _calculate_p_value,
    _calculate_se,
    _calculate_z_statistic,
    _persist_verification,
    _get_descriptive_statistics,
)

# --- _calculate_z_statistic ---

def test_z_statistic_mu_less_threshold():
    req = MagicMock()
    req.threshold = 0.8

    result = _calculate_z_statistic(req, n=50, mu=0.6, sigma=0.1)

    assert result < 0


def test_z_statistic_mu_greater_threshold():
    req = MagicMock()
    req.threshold = 0.8

    result = _calculate_z_statistic(req, n=50, mu=0.9, sigma=0.1)

    assert result > 0


def test_z_statistic_sigma_zero_mu_greater_threshold():
    req = MagicMock()
    req.threshold = 0.8

    result = _calculate_z_statistic(req, n=50, mu=0.9, sigma=0)

    assert result == math.inf


def test_z_statistic_sigma_zero_mu_less_threshold():
    req = MagicMock()
    req.threshold = 0.8

    result = _calculate_z_statistic(req, n=50, mu=0.6, sigma=0)

    assert result == -math.inf


def test_z_statistic_sigma_zero_mu_equals_threshold():
    req = MagicMock()
    req.threshold = 0.8

    result = _calculate_z_statistic(req, n=50, mu=0.8, sigma=0)

    assert result == 0.0


# --- _calculate_p_value ---

def test_p_value_greater_positive_z():
    req = MagicMock()
    req.alternative = "greater"

    result = _calculate_p_value(req, z=2.0)

    assert result < 0.5


def test_p_value_less_positive_z():
    req = MagicMock()
    req.alternative = "less"

    result = _calculate_p_value(req, z=2.0)

    assert result > 0.5


def test_p_value_two_sided_z_zero():
    req = MagicMock()
    req.alternative = "two-sided"

    result = _calculate_p_value(req, z=0)

    assert result == pytest.approx(1.0)


def test_p_value_two_sided_symmetry():
    # two-sided p(z) == 2 * less p(-z) by normal distribution symmetry
    req_two_sided = MagicMock()
    req_two_sided.alternative = "two-sided"
    req_less = MagicMock()
    req_less.alternative = "less"

    p_two_sided = _calculate_p_value(req_two_sided, z=1.5)
    p_less_neg = _calculate_p_value(req_less, z=-1.5)

    assert p_two_sided == pytest.approx(2 * p_less_neg)


def test_p_value_greater_inf_z():
    req = MagicMock()
    req.alternative = "greater"

    result = _calculate_p_value(req, z=math.inf)

    assert result == pytest.approx(0.0)


# --- _calculate_se ---

def test_se_standard_case():
    result = _calculate_se(n=100, sigma=2.0)

    assert result == pytest.approx(2.0 / math.sqrt(100))


def test_se_sigma_zero():
    result = _calculate_se(n=50, sigma=0)

    assert result == 0.0


# --- _calculate_critical_value ---

def test_critical_value_alpha_005():
    req = MagicMock()
    req.alpha = 0.05

    result = _calculate_critical_value(req)

    assert result == pytest.approx(1.96, abs=0.01)


def test_critical_value_alpha_001():
    req = MagicMock()
    req.alpha = 0.01

    result = _calculate_critical_value(req)

    assert result == pytest.approx(2.576, abs=0.01)


# --- _calculate_confidence_interval ---

def test_confidence_interval_se_zero():
    result = _calculate_confidence_interval(mu=0.8, z_crit=1.96, se=0)

    assert result == (0.8, 0.8)


def test_confidence_interval_non_zero_se():
    mu = 0.8
    lower, upper = _calculate_confidence_interval(mu=mu, z_crit=1.96, se=0.1)

    assert lower < mu < upper


# --- _build_result ---

def test_build_result_passed_true():
    req = MagicMock()
    req.threshold = 0.7
    req.alternative = "greater"
    req.alpha = 0.05

    result = _build_result(req, n=50, mu=0.8, sigma=0.1, z=2.0, p_value=0.02, ci=(0.77, 0.83))

    assert result.passed is True


def test_build_result_passed_false():
    req = MagicMock()
    req.threshold = 0.7
    req.alternative = "greater"
    req.alpha = 0.05

    result = _build_result(req, n=50, mu=0.8, sigma=0.1, z=2.0, p_value=0.08, ci=(0.77, 0.83))

    assert result.passed is False


def test_build_result_infinite_z_not_rounded():
    req = MagicMock()
    req.threshold = 0.7
    req.alternative = "greater"
    req.alpha = 0.05

    result = _build_result(req, n=50, mu=0.8, sigma=0.0, z=math.inf, p_value=0.0, ci=(0.8, 0.8))

    assert result.z_statistic == math.inf


def test_build_result_finite_z_rounded():
    req = MagicMock()
    req.threshold = 0.7
    req.alternative = "greater"
    req.alpha = 0.05

    result = _build_result(
        req, n=50, mu=0.8, sigma=0.1, z=1.23456789, p_value=0.02, ci=(0.77, 0.83)
    )

    assert result.z_statistic == round(1.23456789, 6)


def test_build_result_all_fields():
    req = MagicMock()
    req.threshold = 0.7
    req.alternative = "greater"
    req.alpha = 0.05

    result = _build_result(req, n=50, mu=0.8, sigma=0.1, z=2.0, p_value=0.02, ci=(0.77, 0.83))

    assert result.n == 50
    assert result.mean == round(0.8, 6)
    assert result.std == round(0.1, 6)
    assert result.threshold == 0.7
    assert result.alternative == "greater"
    assert result.alpha == 0.05
    assert result.confidence_interval == (0.77, 0.83)


# --- _persist_verification ---

def _make_z_result() -> ZTestResult:
    return ZTestResult(
        n=50, mean=0.8, std=0.1, threshold=0.7, alternative="greater",
        z_statistic=2.0, p_value=0.02, alpha=0.05, passed=True,
        confidence_interval=(0.77, 0.83),
    )


def _make_req() -> MagicMock:
    req = MagicMock()
    req.threshold = 0.7
    req.alpha = 0.05
    req.alternative = "greater"
    return req


def test_persist_verification_calls_session_add():
    session = AsyncMock()

    asyncio.run(_persist_verification(_make_z_result(), _make_req(), session))

    session.add.assert_called_once()


def test_persist_verification_model_fields():
    session = AsyncMock()

    asyncio.run(_persist_verification(_make_z_result(), _make_req(), session))

    model = session.add.call_args.args[0]
    assert isinstance(model, StatisticalVerificationModel)
    assert model.z_statistic == 2.0
    assert model.p_value == 0.02
    assert model.passed is True
    assert model.threshold == 0.7
    assert model.alpha == 0.05
    assert model.alternative == "greater"


def test_persist_verification_calls_commit():
    session = AsyncMock()

    asyncio.run(_persist_verification(_make_z_result(), _make_req(), session))

    session.commit.assert_called_once()
    

# --- _get_descriptive_statistics ---

def test_get_descriptive_statistics():
    req = MagicMock()
    req.scores = [0.7, 0.83, 0.92, 0.12, 1, 0.42]
    
    n, mu, sigma = _get_descriptive_statistics(req)

    assert n == 6
    assert mu == 0.665
    assert round(sigma, 4) == 0.3355
    