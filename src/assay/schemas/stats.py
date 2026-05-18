from typing import Literal

from pydantic import BaseModel, Field


class ZTestRequest(BaseModel):
    """Input for a one-sample z-test checking whether a set of metric scores clears a threshold."""

    scores: list[float] = Field(
        ...,
        min_length=2,
        description="Metric scores from evaluations (e.g. 100 ROUGE scores).",
    )
    threshold: float = Field(
        ...,
        description="Value the population mean must exceed (or fall below) to pass.",
    )
    alpha: float = Field(
        default=0.05,
        gt=0,
        lt=1,
        description="Significance level. 0.05 means 95% confidence.",
    )
    alternative: Literal["greater", "less", "two-sided"] = Field(
        default="greater",
        description=(
            "Direction of the alternative hypothesis. "
            "'greater' tests H1: μ > threshold (most common for quality gates)."
        ),
    )


class ZTestResult(BaseModel):
    """Result of a one-sample z-test."""

    n: int
    mean: float
    std: float
    threshold: float
    alternative: str
    z_statistic: float
    p_value: float
    alpha: float
    # True when p_value < alpha — sufficient evidence to reject H0 at the given confidence level.
    passed: bool
    # Two-sided (1 - alpha) confidence interval for the true population mean.
    confidence_interval: tuple[float, float]
