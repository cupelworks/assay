# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
from typing import TypeVar

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import JudgeCheckModel, SettingsModel, SettingsSection, TargetCheckModel
from assay.schemas import JudgeCheck, JudgeSettings, TargetCheck, TargetSettings
from assay.schemas.settings import settings_errors

GroupSettings = TypeVar("GroupSettings", TargetSettings, JudgeSettings)


async def _find_target_row(session: AsyncSession) -> SettingsModel | None:
    """The `target` group's saved row, or None when it has never been saved
    (or was reset) and the environment is in effect."""
    return await session.get(SettingsModel, SettingsSection.target)


async def _find_judge_row(session: AsyncSession) -> SettingsModel | None:
    """The `judge` group's saved row, or None when the environment is in effect."""
    return await session.get(SettingsModel, SettingsSection.judge)


def _apply_or_422(
        current: GroupSettings,
        changes: BaseModel,
        loc: tuple[str, ...] = ("body",),
        schema: type[GroupSettings] = TargetSettings,
) -> GroupSettings:
    """current with the fields sent in changes applied, validated as a whole.

    Only fields the caller actually sent are applied — an explicit null
    included (`url: null` unsets the URL), an absent field never. headers and
    body replace the current ones entirely.

    Raises:
        RequestValidationError: 422 in FastAPI's own shape, one item per
            problem, every problem at once, each loc pointing at the field
            as the caller sent it (loc is the path to the changes in the
            request body). The submitted values are never echoed back.
    """
    merged = {
        # the settings themselves, without a Read's source/updated_at
        **current.model_dump(include=set(schema.model_fields)),
        **changes.model_dump(exclude_unset=True),
    }
    try:
        return schema(**merged)
    except ValidationError as exc:
        raise RequestValidationError([
            {**error, "loc": (*loc, *error["loc"])} for error in settings_errors(exc)
        ]) from None


def _target_check_schema(check: TargetCheckModel) -> TargetCheck:
    return TargetCheck(
        id=check.id,
        status=check.status,
        created_at=check.created_at,
        completed_at=check.completed_at,
        settings=TargetSettings.model_validate(check.settings),
        ok=check.ok,
        status_code=check.status_code,
        latency_ms=check.latency_ms,
        answer=check.answer,
        error=check.error,
    )


def _judge_check_schema(check: JudgeCheckModel) -> JudgeCheck:
    return JudgeCheck(
        id=check.id,
        status=check.status,
        created_at=check.created_at,
        completed_at=check.completed_at,
        settings=JudgeSettings.model_validate(check.settings),
        ok=check.ok,
        status_code=check.status_code,
        latency_ms=check.latency_ms,
        answer=check.answer,
        error=check.error,
    )
