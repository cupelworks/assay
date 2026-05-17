# Import all models so Base.metadata is fully populated when Alembic inspects it.
# Without these imports, autogenerate would see an empty schema and drop all tables.
from assay.models.base import Base
from assay.models.run import EvaluationRunModel, MetricScoreModel, TestCaseResultModel
from assay.models.suite import SuiteModel, TestCaseModel

__all__ = [
    "Base",
    "EvaluationRunModel",
    "MetricScoreModel",
    "SuiteModel",
    "TestCaseModel",
    "TestCaseResultModel",
]
