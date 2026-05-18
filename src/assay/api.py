from fastapi import APIRouter

from assay.schemas import ZTestRequest, ZTestResult

router = APIRouter()


@router.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    path="/statistical-tests/z-test",
    tags=["analysis"],
    response_model=ZTestResult,
)
def z_test(request: ZTestRequest) -> ZTestResult:
    """One-sample z-test for metric score distributions.

    Tests whether the population mean of `scores` is statistically different from
    `threshold` at the given significance level (`alpha`).

    Typical use: pass 100 ROUGE scores and a minimum quality threshold — the
    response tells you whether the difference is statistically significant or
    could be due to chance.

    **Interpretation**
    - `passed: true` — reject H0; sufficient evidence the true mean clears the threshold.
    - `p_value` — probability of observing this result if H0 were true; lower is stronger evidence.
    - `confidence_interval` — two-sided (1 - alpha) CI for the true population mean.

    **Note:** reliable for n ≥ 30. For smaller samples the t-distribution would be more appropriate.
    """
    from assay.stats import run_z_test

    return run_z_test(request)