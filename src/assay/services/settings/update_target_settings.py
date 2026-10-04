import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import SettingsModel, SettingsSection
from assay.schemas import SettingsSource, TargetSettingsRead, TargetSettingsUpdate
from assay.services.settings._common import _apply_or_422, _find_target_row
from assay.target_settings import resolve_target_settings

logger = logging.getLogger(__name__)


async def update_target_settings(
        request: TargetSettingsUpdate,
        session: AsyncSession,
) -> TargetSettingsRead:
    """Change some of the application-under-test settings and save them.

    The fields sent are applied to the settings currently in effect and the
    result is validated as a whole, then saved as the group's complete row.
    On the first save the
    starting point is the environment's settings, so the saved row carries
    them forward; from then on the row is the whole truth and the
    ASSAY_TARGET_* variables no longer apply. Two saves at once: the last
    one wins.

    Args:
        request: The fields to change; absent fields keep their value.
        session: Active async database session.

    Returns:
        The saved settings, source `database`.

    Raises:
        RequestValidationError: 422 listing every problem with the result.
            Nothing is saved.
    """
    row = await _find_target_row(session)
    first_save = row is None
    saved = _apply_or_422(resolve_target_settings(row), request)

    now = datetime.now().astimezone()
    value = saved.model_dump(mode="json")
    if first_save:
        session.add(SettingsModel(section=SettingsSection.target, value=value, updated_at=now))
    else:
        row.value = value
        row.updated_at = now
    await session.commit()

    # field names only: a URL can carry a token in its query string
    changed_fields = sorted(request.model_fields_set)
    logger.info(
        "Saved the application-under-test settings (%s): %s",
        "first save" if first_save else "update", ", ".join(changed_fields) or "no fields",
        extra={"changed_fields": changed_fields, "first_save": first_save},
    )
    return TargetSettingsRead(**saved.model_dump(), source=SettingsSource.database,
                              updated_at=now)
