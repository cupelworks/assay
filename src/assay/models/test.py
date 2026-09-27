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
#      input, expected_output, and test_type_assignments from the live TestModel. From
#      that point on, changes to the live test do NOT propagate into the set —
#      this mirrors the behavior of test management tools like Jira/Zephyr, where
#      a test set represents a stable, auditable baseline. The snapshot itself
#      can still be edited directly (PATCH) up until it has been run at least
#      once, at which point it freezes so run history stays reproducible. The
#      live TestModel remains editable and can be snapshotted again into the
#      same or different test sets at any time.
#
#      Test type names (not UUIDs) are stored in the snapshot as a JSON list of
#      strings. This keeps the snapshot self-contained and human-readable, and
#      consistent with the name-based FK used in TestTypeAssignmentModel.
#
#      A test set can also be executed directly, independent of any plan — each
#      trigger event is grouped under a TestSetExecutionModel, either a live
#      fan-out over the set's current entries or a replay of a prior
#      execution's exact entries.
#
#   3. TEST PLAN (TestPlanModel + TestPlanEntryModel)
#      A named collection of test sets to be executed together as a campaign.
#      A test plan references test sets (not individual tests), so the unit of
#      organization is always the set. Each trigger event is grouped under a
#      TestPlanExecutionModel (live fan-out or replay, same shape as test set
#      execution) and fans out into one TestRunModel per TestSetEntryModel
#      across all included test sets.
#
# ──────────────────────────────────────────────────────────────────────────────
# EXECUTION LIFECYCLE
# ──────────────────────────────────────────────────────────────────────────────
#
#   A TestRunModel represents a single evaluation attempt. There are three modes:
#
#   - STANDALONE: test_id is set, everything else null.
#     The user runs a live test directly, outside any set or plan. When the
#     run is created the test is copied into a StandaloneRunModel (same id as
#     the run), and the run is evaluated against that copy — later edits to
#     the live test never change it. Has no live/replay pair: each standalone
#     run copies the test as it is at that moment.
#
#   - TEST-SET-TRIGGERED: test_set_entry_id + test_set_execution_id are set.
#     The run belongs to a standalone test set execution (TestSetExecutionModel),
#     grouped by trigger event. Input and configuration are read from the
#     TestSetEntryModel snapshot, guaranteeing reproducibility regardless of
#     subsequent edits to the live test. Once a run references an entry, the
#     entry itself also freezes (see TestSetEntryModel), so the run's record of
#     what it executed against stays accurate too.
#
#   - TEST-PLAN-TRIGGERED: test_set_entry_id + test_plan_execution_id are set.
#     Same reproducibility guarantee as test-set-triggered, but the run was
#     produced by executing a test plan that has this entry's test set linked
#     to it (TestPlanExecutionModel), rather than by executing the test set
#     directly. Which plan is reached via test_plan_execution_id ->
#     TestPlanExecutionModel.test_plan_id, not a separate column here.
#
#   Invariant enforced at the service layer: exactly one of the three column
#   patterns above holds per run. test_set_execution_id and test_plan_execution_id
#   are never both set on the same run — a run is triggered by exactly one path,
#   even though the same TestSetEntryModel can accumulate runs from both paths
#   over its lifetime, across different trigger events.
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
#   TestRunModel is also grouped by trigger event, via exactly one of:
#     - TestSetExecutionModel (standalone test set execution)
#     - TestPlanExecutionModel (execution via a plan that links the set)
#   Navigate the other way with test_set_execution.runs / test_plan_execution.runs.
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
#   dataset_row_id has ondelete="SET NULL": deleting a DatasetRowModel (directly,
#   via a full dataset replace, or via dataset deletion) clears the pointer on any
#   referencing TestModel rather than blocking the delete or cascading. This does
#   not weaken coverage tracking — a deleted row can no longer be "covered" or
#   "uncovered" either way, since coverage is only ever asked of rows that still
#   exist.
#
# ──────────────────────────────────────────────────────────────────────────────


class TestStatus(StrEnum):
    pending = "Pending"
    running = "Running"
    green = "Green"
    amber = "Amber"
    red = "Red"
    not_ran = "NotRan"


# The four values a run doesn't move on from — every other status
# (pending, running) still has work ahead of it. Kept next to TestStatus
# itself so anything needing "is this run done" checks the same set,
# rather than each caller re-deriving its own idea of which values count.
TERMINAL_STATUSES = frozenset({
    TestStatus.green,
    TestStatus.amber,
    TestStatus.red,
    TestStatus.not_ran,
})


class TestTypes(StrEnum):
    deterministic = "deterministic"
    nlp_metric = "nlp_metric"
    llm_as_judge = "llm_as_judge"


class TestTypesCost(StrEnum):
    very_fast = "very_fast"
    fast = "fast"
    expensive = "expensive"


class Comparison(StrEnum):
    """How a threshold-scored test type turns its score into passed.

    gte: higher is better, passed = score >= threshold (every metric today).
    lte: lower is better, passed = score <= threshold (a future distance or
    error-rate metric). Null on the catalogue row for a type that isn't
    scored against a threshold at all (deterministic checks, LLM judges).
    """
    gte = "gte"
    lte = "lte"


class ConfigFieldKind(StrEnum):
    """What kind of value a TestTypesModel.config_fields entry holds.

    "reference" is reserved: a field of this kind is never stored per
    assignment, it always resolves to the test case's own expected_output
    (see docs/test_type_config/dev_notes.md). Every other kind is
    per-assignment free text, stored under TestTypeAssignmentModel.config /
    TestSetEntryModel.test_type_assignments, keyed by the field's own key.
    """
    reference = "reference"
    multiline = "multiline"
    rubric = "rubric"
    numeric = "numeric"


class TestTypesModel(Base):
    """
    A catalogue entry describing a supported evaluation method.

    TestTypesModel is a reference table populated at setup time (e.g. via
    seed data). It describes the available evaluation strategies — deterministic
    checks, NLP metrics, or LLM-as-judge — along with metadata that helps the
    user choose the right one (cost, limitations, what config it needs when
    assigned — see config_fields).

    Tests assign test types via TestTypeAssignmentModel, a junction table that
    references this model by name rather than UUID. This keeps assignments
    stable if the table is ever reseeded — name is the stable, human-readable
    identifier, while id is internal only.

    A row also says how its type is evaluated, as data: engine names the
    code that scores it (a small, fixed set of generic engines in
    worker/evaluators), engine_settings carries that engine's parameters for
    this type, and comparison says which way a threshold-scored type passes.
    Engines are the kitchen appliances, rows are the recipes: a new type that
    only needs an existing engine with different settings (a "ROUGE-1" next
    to "ROUGE", a case-insensitive "Exact Match") is a new row, not new code
    (docs/evaluators/dev_notes.md notes 4 and 5).
    """

    __tablename__ = "test_types"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    category: Mapped[TestTypes] = mapped_column(SAEnum(TestTypes), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    best_for: Mapped[str] = mapped_column(Text, nullable=True)
    cost: Mapped[TestTypesCost | None] = mapped_column(SAEnum(TestTypesCost), nullable=True)
    limitations: Mapped[str] = mapped_column(Text, nullable=True)
    # Seeded server-side only, never user-editable through the API. Each item:
    # {"key": str, "label": str, "kind": str, "required": bool, "min": float |
    # None, "max": float | None}. kind "reference" is reserved — it never gets
    # its own storage, it always resolves to the test case's own
    # expected_output (see docs/test_type_config/dev_notes.md). min/max are
    # only ever set for kind "numeric" (advisory bounds, not enforced by the
    # API — see note 8); omitted (→ None) for every other kind. Empty list for
    # a self-contained type that needs no extra input.
    config_fields: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # Plain text, not an enum: the engine list grows with code, and a row
    # naming an engine this worker doesn't have must fail that one type at
    # run time (detail says which engine is missing), never fail to load.
    engine: Mapped[str] = mapped_column(Text, nullable=False)
    # The engine's parameters for this type — e.g. {"variant": "rougeL"} for
    # ROUGE, {"default_rubric": "..."} for a judge type. Shape is per engine;
    # {} for an engine that takes nothing.
    engine_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # Only for types scored against their threshold config field; null for
    # the rest. Together with that field's min/max (the type's native score
    # range) it fully describes how a score becomes passed.
    comparison: Mapped[Comparison | None] = mapped_column(SAEnum(Comparison), nullable=True)
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
    DatasetRowModel, the dataset_row_id traces back to it for coverage
    reporting, but the test owns its own data and changes to the source
    dataset row are never propagated here. The pointer isn't permanent,
    though — deleting the source row clears it (ondelete="SET NULL") rather
    than blocking the delete, since nothing about the test's own data
    depends on the row still existing.

    When added to a TestSetModel, a snapshot (TestSetEntryModel) is created
    from the test's current state. That snapshot never re-syncs from this
    live test again, though it remains directly editable in its own right
    until it has been run at least once (see TestSetEntryModel). The live
    TestModel continues to be editable and can be snapshotted multiple times
    into different sets.

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
    # None if the test was created manually. ondelete="SET NULL": the test
    # already owns a copy of its own input/expected_output/model_output, so
    # deleting the source row has nothing to protect — the pointer is simply
    # cleared instead of blocking the delete.
    dataset_row_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_rows.id", ondelete="SET NULL"), nullable=True
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
    # Per-assignment config values, keyed by the test type's config_fields[].key.
    # Null if the type has no non-reference config fields. A "reference"-kind
    # field is never stored here — it always resolves to the live test's own
    # expected_output (see docs/test_type_config/dev_notes.md).
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    test: Mapped["TestModel"] = relationship(back_populates="test_type_assignments",
                                             overlaps="test_types")
    test_type: Mapped["TestTypesModel"] = relationship(viewonly=True)


class TestSetModel(Base):
    """
    A named, stable collection of test snapshots.

    A test set is the organizational unit between individual tests and test
    plans. When a test is added to a set, its current state is copied into a
    TestSetEntryModel. That snapshot never re-syncs from the live test again —
    the set always represents the baseline it was given, regardless of how the
    live tests evolve — but each entry can still be edited directly until it
    has been run at least once, after which it freezes (see TestSetEntryModel).

    A test set can be executed directly, independent of any plan — each
    trigger is grouped under a TestSetExecutionModel, which can be a live
    fan-out over the set's current entries or a replay of a prior
    execution's exact entries.

    Test sets can be included in one or more TestPlanModels via
    TestPlanEntryModel junction records.
    """

    __tablename__ = "test_sets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    # passive_deletes lets the database's ON DELETE CASCADE remove the entries,
    # instead of the ORM trying to null out their (NOT NULL) test_set_id first.
    entries: Mapped[list["TestSetEntryModel"]] = relationship(
        back_populates="test_set",
        passive_deletes=True,
    )
    plan_entries: Mapped[list["TestPlanEntryModel"]] = relationship(back_populates="test_set")


class TestSetExecutionModel(Base):
    """
    A single execution of a TestSetModel — groups the TestRunModel rows
    produced by one run of the set.

    An execution is either:

    LIVE (replayed_execution_id is None):
        Fans out over the set's current entries at execution time.

    REPLAY (replayed_execution_id set):
        Re-runs the exact entry set of the referenced prior execution,
        preserving the reproducibility guarantee even if the set's
        entries have since changed.
    """

    __tablename__ = "test_set_executions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    test_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_sets.id"), nullable=False, index=True
    )
    replayed_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_set_executions.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    test_set: Mapped["TestSetModel"] = relationship()
    replayed_execution: Mapped["TestSetExecutionModel | None"] = relationship(remote_side=[id])
    runs: Mapped[list["TestRunModel"]] = relationship(back_populates="test_set_execution")


class TestSetEntryModel(Base):
    """
    A snapshot of a TestModel at the moment it was added to a TestSetModel.

    This is the record that test set executions run against. It captures input,
    expected_output, and test_type_assignments exactly as they were at snapshot
    time. Subsequent edits to the originating TestModel never propagate here.

    The entry itself is directly editable (PATCH) until it has been referenced
    by at least one TestRunModel — at that point it freezes and further edits
    are rejected with a 409, so a run's record of what it executed against
    always stays accurate.

    Test type assignments are stored as a JSON list of {name, config} objects,
    keyed by name (not UUID) for human-readability and resilience against
    table rebuilds — consistent with the name-based FK used in
    TestTypeAssignmentModel, which is where each assignment's config is
    copied from at snapshot time.

    The test_id FK is kept for traceability — it allows navigating from a
    snapshot back to the current live test — but it is never used to pull or
    sync live data into this record.

    Runs produced by test-set-triggered or test-plan-triggered executions
    reference this model, not the live TestModel, ensuring full reproducibility.
    """

    __tablename__ = "test_set_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    test_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_sets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Traceability FK — points back to the live test this snapshot was taken from.
    # Never used to sync or refresh snapshot data.
    test_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tests.id"), nullable=False
    )

    # Snapshot fields — copied from TestModel at inclusion time, and directly
    # editable thereafter until the entry has been run (see class docstring).
    name: Mapped[str] = mapped_column(Text, nullable=False)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Snapshot of test type assignments at inclusion time. Each item:
    # {"name": str, "config": dict | null} — name (not UUID) for
    # human-readability and resilience against table rebuilds, config copied
    # from the live TestTypeAssignmentModel.config at snapshot time. A
    # "reference"-kind field needs no entry here — it resolves from this
    # entry's own expected_output above, already frozen.
    test_type_assignments: Mapped[list[dict]] = mapped_column(JSON, default=list)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    test_set: Mapped["TestSetModel"] = relationship(back_populates="entries")
    test: Mapped["TestModel"] = relationship(back_populates="set_entries")
    runs: Mapped[list["TestRunModel"]] = relationship(
        back_populates="test_set_entry",
        foreign_keys="TestRunModel.test_set_entry_id",
        passive_deletes=True,
    )


class TestPlanModel(Base):
    """
    A named campaign — a collection of test sets to be executed together.

    A test plan operates at the test set level, not the individual test level.
    Each trigger event is grouped under a TestPlanExecutionModel — either a
    live fan-out over the plan's currently linked test sets, or a replay of
    a prior execution's exact entries — and fans out into one TestRunModel
    per TestSetEntryModel across all included test sets. Executing an entry
    also freezes it (see TestSetEntryModel), preserving the reproducibility
    guarantee throughout the entire campaign.
    """

    __tablename__ = "test_plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    entries: Mapped[list["TestPlanEntryModel"]] = relationship(
        back_populates="test_plan",
        passive_deletes=True,
    )


class TestPlanExecutionModel(Base):
    """
    A single execution of a TestPlanModel — groups the TestRunModel rows
    produced by one campaign run.

    An execution is either:

    LIVE (replayed_execution_id is None):
        Fans out over the plan's current entries at execution time.

    REPLAY (replayed_execution_id set):
        Re-runs the exact entry set of the referenced prior execution,
        preserving the reproducibility guarantee even if the plan's
        test sets have since changed.
    """

    __tablename__ = "test_plan_executions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    test_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_plans.id"), nullable=False, index=True
    )
    # Null for a live execution. Set for a replay — points at the
    # execution whose entry set this one re-ran.
    replayed_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_plan_executions.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    test_plan: Mapped["TestPlanModel"] = relationship()
    # Python-side lookup of replayed_execution_id: the ORM object for the
    # prior execution this one replayed (None for a live execution).
    # remote_side=[id] is required because this FK is self-referential —
    # it tells SQLAlchemy that `id` identifies the *other* row, not this one.
    replayed_execution: Mapped["TestPlanExecutionModel | None"] = relationship(remote_side=[id])
    runs: Mapped[list["TestRunModel"]] = relationship(back_populates="test_plan_execution")


class TestPlanEntryModel(Base):
    """
    Junction record linking a TestPlanModel to one of its TestSetModels.

    A test plan can include multiple test sets, and the same test set can
    appear in multiple plans. This join table models that many-to-many
    relationship.

    Unlike TestSetEntryModel, this link deliberately never freezes — a
    plan's set of linked test sets can keep changing at any time, even
    after the plan has been executed. A live execution always fans out
    over whatever test sets are linked right now; past executions stay
    intact regardless, since they're anchored to frozen TestSetEntryModel
    rows via TestPlanExecutionModel, not to this junction table.
    """

    __tablename__ = "test_plan_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    test_plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_plans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    test_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_sets.id"), nullable=False, index=True
    )

    test_plan: Mapped["TestPlanModel"] = relationship(back_populates="entries")
    test_set: Mapped["TestSetModel"] = relationship(back_populates="plan_entries")


class TestRunModel(Base):
    """
    A single evaluation attempt for a test — the atomic unit of execution.

    A run can be initiated in three modes:

    STANDALONE (test_id set, everything else null):
        The user runs a live TestModel directly, outside any set or plan.
        When the run is created, the test is copied into a
        StandaloneRunModel sharing this run's id (see standalone_run), and
        input and configuration are read from that frozen copy — so, like
        the other two modes, the run stays reproducible regardless of later
        edits to the live test. Use this for quick, ad-hoc evaluation
        during test authoring. Has no live/replay pair, unlike the other
        two modes.

    TEST-SET-TRIGGERED (test_set_entry_id + test_set_execution_id set):
        The run is part of a standalone test set execution. Input and
        configuration are read from the TestSetEntryModel snapshot,
        guaranteeing that the run is always reproducible regardless of
        subsequent edits to the live test. Once a run exists, the entry
        itself also rejects further direct edits (see TestSetEntryModel).

    TEST-PLAN-TRIGGERED (test_set_entry_id + test_plan_execution_id set):
        Same reproducibility guarantee as test-set-triggered, but the run was
        produced by executing a test plan that has this entry's test set
        linked to it, rather than by executing the test set directly. Which
        plan is reached via test_plan_execution_id -> TestPlanExecutionModel
        .test_plan_id — there is no separate test_plan_id column here, since
        that would just be the same value one join away.

    Invariant (enforced at the service layer):
        Exactly one of these three column patterns holds per run:
          - test_id set; test_set_entry_id, test_set_execution_id, and
            test_plan_execution_id all null.
          - test_set_entry_id + test_set_execution_id set; test_id and
            test_plan_execution_id null.
          - test_set_entry_id + test_plan_execution_id set; test_id and
            test_set_execution_id null.
        test_set_execution_id and test_plan_execution_id are never both set
        on the same run — a run is triggered by exactly one path, even though
        the same TestSetEntryModel can accumulate runs from both paths over
        its lifetime, across different trigger events.

    Results are written back to this record once execution reaches a
    terminal status. status itself is the outcome, not just the lifecycle
    stage: Green (every assigned test type passed), Amber (some passed,
    some didn't), and Red (every assigned type was evaluated and none
    passed) are all backed by results, one entry per assigned test type
    keyed by name. NotRan means nothing could be attempted at all (the
    model couldn't be called, the entry couldn't be read) — error carries
    the reason, and results stays null; NotRan is the only status error is
    ever set for. Statistical verifications produced post-run are linked
    via the statistical_verifications relationship.
    """

    __tablename__ = "test_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Standalone mode — mutually exclusive with all four columns below.
    test_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tests.id"), nullable=True, index=True
    )
    # Set in both TEST-SET-TRIGGERED and TEST-PLAN-TRIGGERED modes — mutually
    # exclusive with test_id. Which of the two modes also depends on whether
    # test_set_execution_id or the test_plan_* pair below is set.
    test_set_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_set_entries.id"), nullable=True, index=True
    )
    # Test-set-triggered mode — mutually exclusive with the test_plan_* pair below.
    test_set_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_set_executions.id"), nullable=True, index=True
    )
    # Test-plan-triggered mode — mutually exclusive with test_set_execution_id.
    test_plan_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_plan_executions.id"), nullable=True, index=True
    )

    test: Mapped["TestModel | None"] = relationship(
        back_populates="runs",
        foreign_keys=[test_id]
    )
    test_set_entry: Mapped["TestSetEntryModel | None"] = relationship(
        back_populates="runs",
        foreign_keys=[test_set_entry_id]
    )
    test_set_execution: Mapped["TestSetExecutionModel | None"] = relationship(
        back_populates="runs"
    )
    test_plan_execution: Mapped["TestPlanExecutionModel | None"] = relationship(
        back_populates="runs"
    )
    # Standalone mode only: the frozen copy of the test this run evaluates.
    # One-to-one, sharing this run's id; None in the other two modes.
    standalone_run: Mapped["StandaloneRunModel | None"] = relationship(
        back_populates="test_run",
        passive_deletes=True,
    )

    status: Mapped[TestStatus] = mapped_column(
        SAEnum(TestStatus, create_constraint=True), default=TestStatus.pending, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    # Populated once status reaches Green/Amber/Red. results is
    # {type_name: {"passed": bool, "score": float | None, "detail": str | None}},
    # one entry per assigned test type. error is only ever set for NotRan —
    # a run-level failure where no per-type result exists at all, distinct
    # from an individual type failing its own pass criterion (which shows
    # up as that type's own results[type_name]["detail"] instead).
    results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    statistical_verifications: Mapped[list["StatisticalVerificationModel"]] = relationship(
        back_populates="test_run",
        # TODO: cascade deletion of statistical_verifications should be opt-in via the API
    )


class StandaloneRunModel(Base):
    """
    The standalone-specific half of a standalone run: a frozen copy of the
    test as it was when the run was created.

    test_runs is still the table that lists every run, standalone or not —
    this table only extends the standalone ones. The two are one-to-one and
    share the same id: a row's primary key is the test_runs.id it belongs
    to (also its foreign key), so there's no second id to track.

    The copy holds the same fields as a TestSetEntryModel snapshot, in the
    same shapes — test_type_assignments is the same list of
    {"name": str, "config": dict | null} objects — so a standalone run is
    evaluated and displayed exactly like a test-set-triggered one. Unlike an
    entry, it has no set membership and is never editable: it belongs to
    exactly one run from the moment it's created, and later edits to the
    live test (reachable through TestRunModel.test_id) never touch it.
    """

    __tablename__ = "standalone_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_runs.id", ondelete="CASCADE"), primary_key=True
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_type_assignments: Mapped[list[dict]] = mapped_column(JSON, default=list)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now().astimezone()
    )

    test_run: Mapped["TestRunModel"] = relationship(back_populates="standalone_run")
