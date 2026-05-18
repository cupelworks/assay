# Import all models so Base.metadata is fully populated when Alembic inspects it.
# Without these imports, autogenerate would see an empty schema and drop all tables.
from assay.models.base import Base
from assay.models.test import ExecutedTestModel, PendingTestModel, StatisticalVerificationModel

__all__ = [
    "Base",
    "ExecutedTestModel",
    "PendingTestModel",
    "StatisticalVerificationModel",
]
