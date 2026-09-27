import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from assay.models.base import Base


class SettingsSection(StrEnum):
    """A group of settings saved together as one row. `target` is the
    application under test; the LLM judge's settings will be another group."""
    target = "target"


class SettingsModel(Base):
    """
    Settings saved from the UI, one row per group, the group's complete
    settings as one JSON object.

    The shape of `value` is defined in code, by the group's Pydantic schema
    (schemas/settings.py — TargetSettings for `target`), which validates it
    on the way in and on the way out; the table only stores it. Adding a
    setting to a group is a new field with a default on that schema, never a
    migration.

    A missing row is meaningful: the group has never been saved (or was
    reset), so its effective settings come from the ASSAY_* environment and
    the code defaults. When the row exists it is the whole truth for its
    group — the two sources are never mixed field by field.
    """

    __tablename__ = "settings"

    section: Mapped[SettingsSection] = mapped_column(
        SAEnum(SettingsSection, create_constraint=True), primary_key=True
    )
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now().astimezone()
    )


class TargetCheckStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"


class TargetCheckModel(Base):
    """
    One check of the application-under-test settings: a single call to the
    application, made by a worker, with the outcome written back here for
    the UI to poll.

    `settings` holds the complete settings checked — the effective ones at
    the time of the request plus any proposed fields — so the worker checks
    what the user asked about, whatever is saved by the time it runs. Nothing
    here is ever copied to the settings table.

    status moves pending -> running (the worker's atomic claim) -> completed.
    The outcome columns are null until completed; on completion `ok` says
    whether a usable answer came back, `answer` holds it when it did and
    `error` the reason when it didn't. status_code and latency_ms are set
    whenever a response came back, whatever it was.
    """

    __tablename__ = "target_checks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now().astimezone()
    )
    status: Mapped[TargetCheckStatus] = mapped_column(
        SAEnum(TargetCheckStatus, create_constraint=True),
        nullable=False, default=TargetCheckStatus.pending,
    )
    input: Mapped[str] = mapped_column(Text, nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, nullable=False)

    ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
