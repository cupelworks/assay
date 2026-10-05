# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
# Import all models so Base.metadata is fully populated when Alembic inspects it.
# Without these imports, autogenerate would see an empty schema and drop all tables.
from assay.models.base import Base
from assay.models.datasets import DatasetModel, DatasetRowModel
from assay.models.settings import (
    JudgeCheckModel,
    SettingsModel,
    SettingsSection,
    TargetCheckModel,
    TargetCheckStatus,
)
from assay.models.statistics import (
    IN_PROGRESS_BATCH_STATUSES,
    BatchStatus,
    StatisticalBatchModel,
    StatisticalComparisonModel,
    StatisticalTestModel,
)
from assay.models.test import (
    JUDGE_ENGINE,
    TERMINAL_STATUSES,
    Comparison,
    ConfigFieldKind,
    OutputSource,
    StandaloneRunModel,
    TestModel,
    TestPlanEntryModel,
    TestPlanExecutionModel,
    TestPlanModel,
    TestRunCheckTypeModel,
    TestRunModel,
    TestSetEntryModel,
    TestSetExecutionModel,
    TestSetModel,
    TestStatus,
    TestTypeAssignmentModel,
    TestTypes,
    TestTypesCost,
    TestTypesModel,
)

__all__ = [
    "JUDGE_ENGINE",
    "IN_PROGRESS_BATCH_STATUSES",
    "TERMINAL_STATUSES",
    "BatchStatus",
    "Base",
    "Comparison",
    "ConfigFieldKind",
    "DatasetModel",
    "DatasetRowModel",
    "OutputSource",
    "SettingsModel",
    "SettingsSection",
    "StandaloneRunModel",
    "StatisticalBatchModel",
    "StatisticalComparisonModel",
    "StatisticalTestModel",
    "JudgeCheckModel",
    "TargetCheckModel",
    "TargetCheckStatus",
    "TestModel",
    "TestPlanEntryModel",
    "TestPlanExecutionModel",
    "TestPlanModel",
    "TestRunCheckTypeModel",
    "TestRunModel",
    "TestSetEntryModel",
    "TestSetExecutionModel",
    "TestSetModel",
    "TestStatus",
    "TestTypeAssignmentModel",
    "TestTypes",
    "TestTypesCost",
    "TestTypesModel",
]
