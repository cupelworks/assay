from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from assay.models import SettingsModel, SettingsSection
from assay.schemas import TargetSettings, TargetSettingsUpdate
from assay.schemas.settings import settings_errors

# the fields of the settings themselves, without TargetSettingsRead's source/updated_at
_TARGET_FIELDS = set(TargetSettings.model_fields)


async def _find_target_row(session: AsyncSession) -> SettingsModel | None:
    """The `target` group's saved row, or None when it has never been saved
    (or was reset) and the environment is in effect."""
    return await session.get(SettingsModel, SettingsSection.target)


def _apply_or_422(
        current: TargetSettings,
        changes: TargetSettingsUpdate,
        loc: tuple[str, ...] = ("body",),
) -> TargetSettings:
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
        **current.model_dump(include=_TARGET_FIELDS),
        **changes.model_dump(exclude_unset=True),
    }
    try:
        return TargetSettings(**merged)
    except ValidationError as exc:
        raise RequestValidationError([
            {**error, "loc": (*loc, *error["loc"])} for error in settings_errors(exc)
        ]) from None
