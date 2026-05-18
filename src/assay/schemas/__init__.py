# Re-exports all public schema classes so callers can import from `assay.schemas`
# directly, without knowing the internal sub-module structure.
# Direct imports (e.g. `from assay.schemas.run import ...`) still work
from assay.schemas.evaluation import EvaluationRequest, JudgeCriterion
from assay.schemas.run import (
    EvaluationRun,
    MetricScore,
    RunStatus,
    StatisticalSummary,
    TestCaseResult,
)
from assay.schemas.stats import ZTestRequest, ZTestResult
from assay.schemas.suite import TestCase, TestSuite

# Defines what `from assay.schemas import *` exposes, and signals to IDEs and
# type checkers which names are part of the public API.
__all__ = [
    "EvaluationRequest",
    "EvaluationRun",
    "JudgeCriterion",
    "MetricScore",
    "RunStatus",
    "StatisticalSummary",
    "TestCase",
    "TestCaseResult",
    "TestSuite",
    "ZTestRequest",
    "ZTestResult",
]
