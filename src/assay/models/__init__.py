# Import all models so Base.metadata is fully populated when Alembic inspects it.
# Without these imports, autogenerate would see an empty schema and drop all tables.
from assay.models.base import Base
from assay.models.datasets import DatasetModel, DatasetRowModel
from assay.models.stats import StatisticalVerificationModel
from assay.models.test import TestRunModel, TestStatus, TestTypes, TestTypesCost, TestTypesModel

__all__ = [
    "Base",
    "DatasetModel",
    "DatasetRowModel",
    "StatisticalVerificationModel",
    "TestRunModel",
    "TestStatus",
    "TestTypes",
    "TestTypesCost",
    "TestTypesModel",
]
