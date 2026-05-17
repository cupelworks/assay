from fastapi import APIRouter, HTTPException, status

from assay.schemas import (
    EvaluationRequest,
    EvaluationRun,
    StatisticalSummary,
    TestCaseResult,
    TestSuite,
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
def evaluate(request: EvaluationRequest) -> TestCaseResult:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Evaluators (NLP metrics + LLM judge) not yet implemented.",
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
