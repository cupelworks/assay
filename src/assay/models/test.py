import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from assay.models.base import Base
from assay.models.datasets import DatasetRowModel

if TYPE_CHECKING:
    from assay.models.stats import StatisticalVerificationModel

# ──────────────────────────────────────────────────────────────────────────────
# DESIGN OVERVIEW
# ──────────────────────────────────────────────────────────────────────────────
#
# The test system is built around three core concepts:
#
#   1. TEST (TestModel)
#      A living, always-editable test definition. The user creates tests either
#      manually or by importing rows from a DatasetModel. A test owns its own
#      input/expected_output/model_output — it is fully independent from the
#      dataset row it may have originated from. The dataset_row_id is kept only
#      for traceability (e.g. computing dataset coverage), never for data sync.
#
#      Test types are assigned via TestTypeAssignmentModel, a junction table
#      that references TestTypesModel by name rather than UUID, making
#      assignments stable against table rebuilds and human-readable in the DB.
#
#   2. TEST SET (TestSetModel + TestSetEntryModel)
#      A named, ordered collection of tests. When a test is added to a test set,
#      a snapshot (TestSetEntryModel) is created at that exact moment — copying
#      input, expected_output, and test_type_names from the live TestModel. From
#      that point on, the snapshot is immutable: changes to the live test do NOT
#      propagate into the set. This mirrors the behavior of test management
#      tools like Jira/Zephyr, where a test set represents a stable, auditable
#      baseline. The live TestModel remains editable and can be snapshotted again
#      into the same or different test sets at any time.
#
#      Test type names (not UUIDs) are stored in the snapshot as a JSON list of
#      strings. This keeps the snapshot self-contained and human-readable, and
#      consistent with the name-based FK used in TestTypeAssignmentModel.
#
#   3. TEST PLAN (TestPlanModel + TestPlanEntryModel)
#      A named collection of test sets to be executed together as a campaign.
#      A test plan references test sets (not individual tests), so the unit of
#      organization is always the set. Executing a test plan fans out into one
#      TestRunModel per TestSetEntryModel across all its sets.
#
# ──────────────────────────────────────────────────────────────────────────────
# EXECUTION LIFECYCLE
# ──────────────────────────────────────────────────────────────────────────────
#
#   A TestRunModel represents a single evaluation attempt. There are two modes:
#
#   - STANDALONE: test_id is set, test_set_entry_id is None.
#     The user runs a live test directly, outside any set or plan. The run
#     reads input/expected_output from the live TestModel at execution time.
#
#   - SET-BASED: test_set_entry_id is set, test_id is None.
#     The run is part of a test set (or plan) execution. Input and configuration
#     are read from the frozen TestSetEntryModel snapshot, guaranteeing
#     reproducibility regardless of subsequent edits to the live test.
#
#   Invariant enforced at the service layer:
#     exactly one of (test_id, test_set_entry_id) must be set — never both,
#     never neither.
#
# ──────────────────────────────────────────────────────────────────────────────
# TRACEABILITY CHAIN
# ──────────────────────────────────────────────────────────────────────────────
#
#   DatasetRowModel
#       └── TestModel (dataset_row_id, traceability only)
#               └── TestSetEntryModel (snapshot at time of set inclusion)
#                       └── TestRunModel (frozen execution)
#                               └── StatisticalVerificationModel (stats results)
#
#   From a live TestModel you can navigate:
#     test.set_entries → entry.runs → run.statistical_verifications
#   to retrieve the full execution history across all sets and plans.
#
# ──────────────────────────────────────────────────────────────────────────────
# DATASET COVERAGE
# ──────────────────────────────────────────────────────────────────────────────
#
#   Coverage is computed via dataset_row_id: a DatasetRowModel is considered
#   "covered" if at least one TestModel references it. Coverage is a traceability
#   concern, not a content-equality concern — even if the test has drifted from
#   the original row, the coverage link remains valid.
#
#   If content drift detection is needed in future (e.g. flagging tests whose
#   input no longer matches the source dataset row), it should be implemented as
#   a separate service-layer check, not enforced at the ORM level.
#
# ──────────────────────────────────────────────────────────────────────────────


class TestStatus(StrEnum):
    pending = "Pending"
    running = "Running"
    completed = "Completed"
    failed = "Failed"


class TestTypes(StrEnum):
    deterministic = "Deterministic"
    nlp_metric = "NLP Metric"
    llm_as_judge = "LLM-As-Judge"


class TestTypesCost(StrEnum):
    very_fast = "Free & Lightning Fast"
    fast = "Free & Fast"
    expensive = "Expensive & Slow"


class TestTypesModel(Base):
    """
    A catalogue entry describing a supported evaluation method.

    TestTypesModel is a reference table populated at setup time (e.g. via
    seed data). It describes the available evaluation strategies — deterministic
    checks, NLP metrics, or LLM-as-judge — along with metadata that helps the
    user choose the right one (cost, limitations, whether a reference output is
    required).

    Tests assign test types via TestTypeAssignmentModel, a junction table that
    references this model by name rather than UUID. This keeps assignments
    stable if the table is ever reseeded — name is the stable, human-readable
    identifier, while id is internal only.
    """

    __tablename__ = "test_types"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    category: Mapped[str] = mapped_column(SAEnum(TestTypes), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    best_for: Mapped[str] = mapped_column(Text, nullable=True)
    cost: Mapped[str] = mapped_column(SAEnum(TestTypesCost), nullable=True)
    limitations: Mapped[str] = mapped_column(Text, nullable=True)
    # If True, the test type requires an expected_output to function correctly.
    required_reference: Mapped[bool] = mapped_column(Boolean, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=True, default=lambda: datetime.now().astimezone()
    )


class TestModel(Base):
    """
    A living, always-editable test definition.

    TestModel is the primary unit of work for the user. It holds the test
    inputs and configuration and can be freely edited at any time. It is
    intentionally decoupled from its origin: if a test was imported from a
    DatasetRowModel, the dataset_row_id is preserved for traceability and
    coverage reporting, but the test owns its own data and changes to the
    source dataset row are never propagated here.

    When added to a TestSetModel, a frozen snapshot (TestSetEntryModel) is
    created from the test's current state. The live TestModel continues to
    be editable and can be snapshotted multiple times into different sets.

    Test types are assigned via TestTypeAssignmentModel, a junction table that
    references TestTypesModel by name rather than UUID. This makes the
    assignment stable against table rebuilds and human-readable in the DB.
    The convenience relationship test_types allows direct access to the full
    TestTypesModel records without going through the junction manually.

    A test can also be run directly (standalone), without being part of any
    set or plan, via a TestRunModel with test_id set and test_set_entry_id
    left null.
    """

    __tablename__ = "tests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Traceability FK — points to the DatasetRowModel this test was imported
    # from, if any. Never used to sync data; only for coverage calculations.
    # None if the test was created manually.
    dataset_row_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_rows.id"), nullable=True
    )
    dataset_row: Mapped["DatasetRowModel | None"] = relationship()

    name: Mapped[str] = mapped_column(Text, nullable=False)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Junction-based test type assignments — queryable relationally.
    # Use test_types for convenient access to the full TestTypesModel records,
    # or test_type_assignments if you need to work with the junction directly.
    test_type_assignments: Mapped[list["TestTypeAssignmentModel"]] = relationship(
        back_populates="test",
        cascade="all, delete-orphan",
    )
    test_types: Mapped[list["TestTypesModel"]] = relationship(
        secondary="test_type_assignments",
        overlaps="test_type_assignments",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    # All snapshots of this test across every test set it has been added to.
    # Navigate via set_entries → runs to retrieve the full execution history.
    set_entries: Mapped[list["TestSetEntryModel"]] = relationship(back_populates="test")

    # Standalone runs only — set-based runs are accessed via set_entries → runs.
    runs: Mapped[list["TestRunModel"]] = relationship(
        back_populates="test",
        foreign_keys="TestRunModel.test_id"
    )


class TestTypeAssignmentModel(Base):
    """
    Junction record assigning a test type to a TestModel.

    Uses test_type_name (the unique name from TestTypesModel) as the FK rather
    than the UUID. This makes assignments stable against table rebuilds and
    keeps the data human-readable directly in the DB — if test_types is
    reseeded with new UUIDs, no assignment records need to be updated.

    The composite primary key (test_id, test_type_name) naturally enforces
    uniqueness — a test type can only be assigned once per test.
    """

    __tablename__ = "test_type_assignments"

    test_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"),
                                               primary_key=True,)
    # References TestTypesModel.name — stable, human-readable, unique.
    test_type_name: Mapped[str] = mapped_column(ForeignKey("test_types.name"), primary_key=True)

    test: Mapped["TestModel"] = relationship(back_populates="test_type_assignments",
                                             overlaps="test_types")
    test_type: Mapped["TestTypesModel"] = relationship(viewonly=True)


class TestSetModel(Base):
    """
    A named, stable collection of frozen test snapshots.

    A test set is the organizational unit between individual tests and test
    plans. When a test is added to a set, its current state is copied into a
    TestSetEntryModel. From that point on the snapshot is immutable — the set
    always represents the same baseline regardless of how the live tests evolve.

    Test sets can be included in one or more TestPlanModels via
    TestPlanEntryModel junction records.
    """

    __tablename__ = "test_sets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    entries: Mapped[list["TestSetEntryModel"]] = relationship(back_populates="test_set")
    plan_entries: Mapped[list["TestPlanEntryModel"]] = relationship(back_populates="test_set")


class TestSetEntryModel(Base):
    """
    An immutable snapshot of a TestModel at the moment it was added to a TestSetModel.

    This is the frozen record that test set executions run against. It captures
    input, expected_output, and test_type_names exactly as they were at snapshot
    time. Subsequent edits to the originating TestModel have no effect here.

    Test type names (not UUIDs) are stored as a JSON list of strings. This keeps
    the snapshot self-contained and human-readable, and consistent with the
    name-based FK used in TestTypeAssignmentModel. The service layer populates
    this by copying [tt.name for tt in test.test_types] at snapshot time.

    The test_id FK is kept for traceability — it allows navigating from a
    snapshot back to the current live test — but it is never used to pull or
    sync live data into this record.

    Runs produced by set-based or plan-based executions reference this model,
    not the live TestModel, ensuring full reproducibility.
    """

    __tablename__ = "test_set_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    test_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_sets.id"), nullable=False, index=True
    )
    # Traceability FK — points back to the live test this snapshot was taken from.
    # Never used to sync or refresh snapshot data.
    test_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tests.id"), nullable=False
    )

    # Immutable snapshot fields — copied from TestModel at inclusion time.
    name: Mapped[str] = mapped_column(Text, nullable=False)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Snapshot of test type names at inclusion time — stored as strings, not
    # UUIDs, for human-readability and resilience against table rebuilds.
    test_type_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    test_set: Mapped["TestSetModel"] = relationship(back_populates="entries")
    test: Mapped["TestModel"] = relationship(back_populates="set_entries")
    runs: Mapped[list["TestRunModel"]] = relationship(
        back_populates="test_set_entry",
        foreign_keys="TestRunModel.test_set_entry_id"
    )


class TestPlanModel(Base):
    """
    A named campaign — a collection of test sets to be executed together.

    A test plan operates at the test set level, not the individual test level.
    Executing a plan fans out into one TestRunModel per TestSetEntryModel
    across all included test sets, preserving the frozen snapshot guarantee
    throughout the entire campaign.
    """

    __tablename__ = "test_plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    entries: Mapped[list["TestPlanEntryModel"]] = relationship(back_populates="test_plan")


class TestPlanEntryModel(Base):
    """
    Junction record linking a TestPlanModel to one of its TestSetModels.

    A test plan can include multiple test sets, and the same test set can
    appear in multiple plans. This join table models that many-to-many
    relationship.
    """

    __tablename__ = "test_plan_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    test_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_plans.id"), nullable=False, index=True
    )
    test_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_sets.id"), nullable=False, index=True
    )

    test_plan: Mapped["TestPlanModel"] = relationship(back_populates="entries")
    test_set: Mapped["TestSetModel"] = relationship(back_populates="plan_entries")


class TestRunModel(Base):
    """
    A single evaluation attempt for a test — the atomic unit of execution.

    A run can be initiated in two modes:

    STANDALONE (test_id set, test_set_entry_id None):
        The user runs a live TestModel directly, outside any set or plan.
        Input and configuration are read from the live test at execution time.
        Use this for quick, ad-hoc evaluation during test authoring.

    SET-BASED (test_set_entry_id set, test_id None):
        The run is part of a test set or plan execution. Input and
        configuration are read from the frozen TestSetEntryModel snapshot,
        guaranteeing that the run is always reproducible regardless of
        subsequent edits to the live test.

    Invariant (enforced at the service layer):
        Exactly one of (test_id, test_set_entry_id) must be non-null.
        Having both set or both null is an invalid state.

    Results (scores, error) are written back to this record on completion.
    Statistical verifications produced post-run are linked via the
    statistical_verifications relationship.
    """

    __tablename__ = "test_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Standalone mode — mutually exclusive with test_set_entry_id.
    test_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tests.id"), nullable=True, index=True
    )
    # Set-based mode — mutually exclusive with test_id.
    test_set_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_set_entries.id"), nullable=True, index=True
    )

    test: Mapped["TestModel | None"] = relationship(
        back_populates="runs",
        foreign_keys=[test_id]
    )
    test_set_entry: Mapped["TestSetEntryModel | None"] = relationship(
        back_populates="runs",
        foreign_keys=[test_set_entry_id]
    )

    status: Mapped[TestStatus] = mapped_column(
        SAEnum(TestStatus, create_constraint=True), default=TestStatus.pending, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    # Populated on completion. scores is a dict of {metric_name: score}.
    # error is set instead of scores if the run failed.
    scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    statistical_verifications: Mapped[list["StatisticalVerificationModel"]] = relationship(
        back_populates="test_run",
        # TODO: cascade deletion of statistical_verifications should be opt-in via the API
    )
