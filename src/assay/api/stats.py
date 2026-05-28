from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import ZTestRequest, ZTestResult
from assay.services import run_z_test

router = APIRouter(tags=["analysis"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    path="/statistical-tests/z-test",
    response_model=ZTestResult,
)
async def z_test(request: ZTestRequest, session: SessionDep) -> ZTestResult:  # pragma: no cover
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

    return await run_z_test(request, session)
