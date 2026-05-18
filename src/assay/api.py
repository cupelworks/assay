import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.models.run import TestCaseResultModel
from assay.schemas import (
    EvaluationRequest,
    EvaluationRun,
    StatisticalSummary,
    TestCaseResult,
    TestSuite,
    ZTestRequest,
    ZTestResult,
)

# Collects all route definitions in this module. main.py mounts it onto the
# FastAPI app via app.include_router(router), keeping route logic separate from
# app setup and making the router independently testable.
router = APIRouter()


@router.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    "/test-suites",
    tags=["suites"],
    response_model=TestSuite,
    status_code=status.HTTP_201_CREATED,
)
def create_test_suite(suite: TestSuite) -> TestSuite:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Test-suite persistence not yet implemented.",
    )


@router.post(
    "/test-suites/{suite_id}/runs",
    tags=["runs"],
    response_model=EvaluationRun,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_run(suite_id: str) -> EvaluationRun:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Run execution not yet implemented.",
    )


@router.post(
    "/evaluations",
    tags=["evaluations"],
    response_model=TestCaseResult,
)
async def evaluate(
    request: EvaluationRequest,
    session: AsyncSession = Depends(get_session),
) -> TestCaseResult:
    # Evaluators (NLP metrics + LLM judge) not yet implemented — scores are empty.
    row = TestCaseResultModel(
        case_id=uuid.uuid4(),
        actual_output=request.actual_output,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    return TestCaseResult(
        case_id=row.case_id,
        actual_output=row.actual_output,
        scores=[],
        latency_ms=row.latency_ms,
        error=row.error,
    )


@router.get(
    "/runs/{run_id}/summary",
    tags=["runs"],
    response_model=list[StatisticalSummary],
)
def run_summary(run_id: str) -> list[StatisticalSummary]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Statistical summary not yet implemented.",
    )


@router.post(
    "/statistical-tests/z-test",
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
