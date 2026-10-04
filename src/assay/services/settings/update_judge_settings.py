import logging

from sqlalchemy.ext.asyncio import AsyncSession

from assay.judge_settings import judge_settings_read, resolve_judge_settings
from assay.models import SettingsModel, SettingsSection
from assay.schemas import JudgeSettings, JudgeSettingsRead, JudgeSettingsUpdate, SettingsSource
from assay.services.settings._common import _apply_or_422, _find_judge_row
from assay.timestamps import utc_now

logger = logging.getLogger(__name__)


async def update_judge_settings(
        request: JudgeSettingsUpdate,
        session: AsyncSession,
) -> JudgeSettingsRead:
    """Change some of the judge settings and save them.

    The fields sent are applied to the settings in effect and the result is
    validated as a whole, then saved as the group's complete row. The first
    save carries the environment's settings forward; from then on the row is
    the whole truth and the ASSAY_JUDGE_* variables no longer apply. Two
    saves at once: the last one wins.

    Args:
        request: The fields to change; absent fields keep their value.
        session: Active async database session.

    Returns:
        The saved settings, source `database`.

    Raises:
        RequestValidationError: 422 listing every problem with the result.
            Nothing is saved.
    """
    row = await _find_judge_row(session)
    first_save = row is None
    saved = _apply_or_422(resolve_judge_settings(row), request, schema=JudgeSettings)

    now = utc_now()
    value = saved.model_dump(mode="json")
    if first_save:
        session.add(SettingsModel(section=SettingsSection.judge, value=value, updated_at=now))
    else:
        row.value = value
        row.updated_at = now
    await session.commit()

    changed_fields = sorted(request.model_fields_set)
    logger.info(
        "Saved the judge settings (%s): %s",
        "first save" if first_save else "update", ", ".join(changed_fields) or "no fields",
        extra={"changed_fields": changed_fields, "first_save": first_save},
    )
    return judge_settings_read(saved, SettingsSource.database, now)
