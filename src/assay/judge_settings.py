"""Which judge settings are in effect, and where from.

The `judge` group's row in the settings table, when there is one, is the
whole truth; with no row, the ASSAY_JUDGE_* environment and the code
defaults are. Shared by the API (GET/PATCH/DELETE /settings/judge, checks)
and the worker (every run with a judge check), each fetching the row with
its own kind of session — this only decides what the row, or its absence,
means.
"""
from datetime import datetime

from assay.config import settings
from assay.models import SettingsModel
from assay.schemas.settings import JudgeSettings, JudgeSettingsRead, SettingsSource


def resolve_judge_settings(row: SettingsModel | None) -> JudgeSettingsRead:
    """The effective settings for the `judge` group.

    Args:
        row: The group's row in the settings table, or None if it has none.

    Raises:
        pydantic.ValidationError: the stored row no longer satisfies the
            rules — a bug to fix with a migration, never silently replaced
            by the environment's values.
    """
    if row is None:
        effective = settings.judge_settings()
        return judge_settings_read(effective, SettingsSource.environment, None)
    return judge_settings_read(JudgeSettings(**row.value), SettingsSource.database,
                               row.updated_at)


def judge_settings_read(effective: JudgeSettings, source: SettingsSource,
                        updated_at: datetime | None) -> JudgeSettingsRead:
    """effective as the API returns it: with the URL the worker calls, the
    variable its key is read from, and where it comes from."""
    return JudgeSettingsRead(
        **effective.model_dump(),
        endpoint=effective.endpoint(),
        api_key_variable=effective.key_variable(),
        source=source,
        updated_at=updated_at,
    )
