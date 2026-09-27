import logging

from sqlalchemy.ext.asyncio import AsyncSession

from assay.schemas import TargetSettingsRead
from assay.services.settings._common import _find_target_row
from assay.target_settings import resolve_target_settings

logger = logging.getLogger(__name__)


async def reset_target_settings(session: AsyncSession) -> TargetSettingsRead:
    """Drop the saved application-under-test settings, so the environment's
    apply again.

    Resetting settings that were never saved isn't an error: the outcome is
    the same.

    Args:
        session: Active async database session.

    Returns:
        The settings now in effect, source `environment`.
    """
    row = await _find_target_row(session)
    if row is not None:
        await session.delete(row)
        await session.commit()
        logger.info("Reset the application-under-test settings to the environment's")
    return resolve_target_settings(None)
