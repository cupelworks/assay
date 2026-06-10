# Import all models so Base.metadata is fully populated when Alembic inspects it.
# Without these imports, autogenerate would see an empty schema and drop all tables.
from assay.models.base import Base
from assay.models.datasets import DatasetModel, DatasetRowModel
from assay.models.stats import StatisticalVerificationModel
from assay.models.test import (
    TestModel,
    TestPlanEntryModel,
    TestPlanModel,
    TestRunModel,
    TestSetEntryModel,
    TestSetModel,
    TestStatus,
    TestTypeAssignmentModel,
    TestTypes,
    TestTypesCost,
    TestTypesModel,
)
from assay.schemas.tests import CreateTestCaseRequest, CreateTestCaseResponse, TestCaseID

__all__ = [
    "Base",
    "CreateTestCaseRequest",
    "CreateTestCaseResponse",
    "DatasetModel",
    "DatasetRowModel",
    "StatisticalVerificationModel",
    "TestCaseID",
    "TestModel",
    "TestPlanEntryModel",
    "TestPlanModel",
    "TestRunModel",
    "TestSetEntryModel",
    "TestSetModel",
    "TestStatus",
    "TestTypeAssignmentModel",
    "TestTypes",
    "TestTypesCost",
    "TestTypesModel",
]
