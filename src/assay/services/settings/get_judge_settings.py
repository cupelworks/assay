# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
from sqlalchemy.ext.asyncio import AsyncSession

from assay.judge_settings import resolve_judge_settings
from assay.schemas import JudgeSettingsRead
from assay.services.settings._common import _find_judge_row


async def get_judge_settings(session: AsyncSession) -> JudgeSettingsRead:
    """The judge settings in effect, the variable the key is read from, and
    where the settings come from: the saved row when there is one, else the
    environment.

    Args:
        session: Active async database session.

    Raises:
        pydantic.ValidationError: the saved row no longer validates — a bug,
            surfaced as a 500 rather than papered over.
    """
    return resolve_judge_settings(await _find_judge_row(session))
