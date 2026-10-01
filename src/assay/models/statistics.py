import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from assay.models.base import Base


class BatchStatus(StrEnum):
    """A batch's lifecycle and outcome (docs/statistics/dev_notes.md note 17).

    Its own enum, not an extension of TestStatus, so a run's status and a
    batch's can't be mixed up in a query. The words a run uses mean the same
    here (Pending, Running, NotRan); the new words are statistical verdicts a
    single run can never reach.
    """
    pending = "Pending"            # created, no run has started
    running = "Running"            # at least one run started, not all finished
    passed = "Passed"              # every applicable check's verdict is pass
    failed = "Failed"              # at least one applicable check's verdict is fail
    inconclusive = "Inconclusive"  # finished; neither proven at this size
    incomplete = "Incomplete"      # stopped by the user before every run ran
    not_ran = "NotRan"             # finished; no run could be evaluated at all


IN_PROGRESS_BATCH_STATUSES = frozenset({BatchStatus.pending, BatchStatus.running})


class StatisticalBatchModel(Base):
    """
    One "run with statistics": a scope run N times as one batch, and the
    statistical test computed over those runs once they've all finished
    (docs/statistics/).

    The scope is exactly one of test_id, test_set_id, test_plan_id. The runs
    and executions it created carry this batch's id and their 1-based
    `batch_index` (the time they belong to); for a standalone test each time
    is one run, for a set or plan one execution with a run per entry.

    `result` is computed by the API the first time a read finds every run
    finished, then stored and returned as is (note 19, decision 4): what was
    verified, with what, when — the statistical claim kept like a run's
    outcome. `status` follows the runs until then. A batch is history: no
    endpoint deletes one.
    """

    __tablename__ = "statistical_batches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    test_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tests.id"), nullable=True, index=True)
    test_set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_sets.id"), nullable=True, index=True)
    test_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_plans.id"), nullable=True, index=True)

    # A name from the catalogue (services/statistics/catalogue.py): plain
    # text, like a check's engine — the catalogue grows with code
    statistical_test: Mapped[str] = mapped_column(Text, nullable=False)
    # Every parameter, defaults filled in, as the batch was created with
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    times_requested: Mapped[int] = mapped_column(Integer, nullable=False)
    runs_per_time: Mapped[int] = mapped_column(Integer, nullable=False)
    # The floor and the calls one time pays for, at creation:
    # {"floor": 29, "calls_per_time": {"application": 2, "judge": 1}}
    plan: Mapped[dict] = mapped_column(JSON, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[BatchStatus] = mapped_column(
        SAEnum(BatchStatus, create_constraint=True), nullable=False,
        default=BatchStatus.pending, index=True)
    # Null until computed; then {"result": <BatchResult>, "progress": <BatchProgress>},
    # the final progress kept with it so a finished batch never reloads its runs
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now().astimezone())
    # Set when a stop cancelled at least one pending run
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Set when the result was computed: every run finished
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StatisticalComparisonModel(Base):
    """
    Two finished batches of the same scope compared check by check: "did my
    change help?" (docs/statistics/ notes 8, 18, 22). Computed when created,
    stored, and read back as is — like a batch's result, the record of what
    was compared, with what, when.

    The scope columns repeat the batches' (both have the same) so comparisons
    can be listed by scope without a join. `result` holds the per-check
    comparison; A is the baseline, every difference is B − A.
    """

    __tablename__ = "statistical_comparisons"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    batch_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("statistical_batches.id"), nullable=False, index=True)
    batch_b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("statistical_batches.id"), nullable=False, index=True)
    test_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tests.id"), nullable=True, index=True)
    test_set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_sets.id"), nullable=True, index=True)
    test_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_plans.id"), nullable=True, index=True)
    statistical_test: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now().astimezone())
