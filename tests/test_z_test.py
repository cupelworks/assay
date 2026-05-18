import math

import pytest
from fastapi.testclient import TestClient

from assay.main import app

client = TestClient(app)


def post(payload: dict) -> dict:
    r = client.post("/statistical-tests/z-test", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def test_clearly_passes():
    # 50 scores well above threshold — should pass with high confidence.
    scores = [0.85] * 50
    result = post({"scores": scores, "threshold": 0.7})
    assert result["passed"] is True
    assert result["p_value"] == 0.0  # zero std → deterministic
    assert result["mean"] == pytest.approx(0.85)


def test_clearly_fails():
    # Scores well below threshold.
    scores = [0.5] * 50
    result = post({"scores": scores, "threshold": 0.7})
    assert result["passed"] is False


def test_large_sample_passes():
    # 100 scores averaging 0.78, threshold 0.7 — should be statistically significant.
    import random
    random.seed(42)
    scores = [0.78 + random.gauss(0, 0.05) for _ in range(100)]
    result = post({"scores": scores, "threshold": 0.7})
    assert result["passed"] is True
    assert result["p_value"] < 0.05
    assert result["n"] == 100


def test_two_sided_alternative():
    scores = [0.75] * 30
    result = post({"scores": scores, "threshold": 0.75, "alternative": "two-sided"})
    # Mean equals threshold exactly — z=0, p=1, should not pass.
    assert result["passed"] is False
    assert result["z_statistic"] == 0.0 or math.isnan(result["z_statistic"]) or result["p_value"] > 0.05


def test_less_alternative():
    # Testing that mean is below 0.9 — should pass when scores are around 0.6.
    scores = [0.6] * 40
    result = post({"scores": scores, "threshold": 0.9, "alternative": "less"})
    assert result["passed"] is True


def test_custom_alpha():
    # Borderline case — passes at alpha=0.10 but not at alpha=0.01.
    import random
    random.seed(0)
    scores = [0.72 + random.gauss(0, 0.08) for _ in range(30)]
    result_relaxed = post({"scores": scores, "threshold": 0.7, "alpha": 0.10})
    result_strict = post({"scores": scores, "threshold": 0.7, "alpha": 0.01})
    # p_value is the same — only the pass threshold differs.
    assert result_relaxed["p_value"] == result_strict["p_value"]
    if result_strict["passed"]:
        assert result_relaxed["passed"]


def test_confidence_interval_contains_mean():
    scores = [0.8 + i * 0.001 for i in range(50)]
    result = post({"scores": scores, "threshold": 0.7})
    lo, hi = result["confidence_interval"]
    assert lo < result["mean"] < hi


def test_validation_rejects_single_score():
    r = client.post("/statistical-tests/z-test", json={"scores": [0.8], "threshold": 0.7})
    assert r.status_code == 422


def test_validation_rejects_invalid_alpha():
    r = client.post(
        "/statistical-tests/z-test",
        json={"scores": [0.8, 0.9], "threshold": 0.7, "alpha": 1.5},
    )
    assert r.status_code == 422
