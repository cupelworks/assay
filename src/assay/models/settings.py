from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime
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
