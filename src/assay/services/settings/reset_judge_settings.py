# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from assay.judge_settings import resolve_judge_settings
from assay.schemas import JudgeSettingsRead
from assay.services.settings._common import _find_judge_row

logger = logging.getLogger(__name__)


async def reset_judge_settings(session: AsyncSession) -> JudgeSettingsRead:
    """Drop the saved judge settings, so the environment's apply again.
    Resetting settings that were never saved isn't an error.

    Args:
        session: Active async database session.

    Returns:
        The settings now in effect, source `environment`.
    """
    row = await _find_judge_row(session)
    if row is not None:
        await session.delete(row)
        await session.commit()
        logger.info("Reset the judge settings to the environment's")
    return resolve_judge_settings(None)
