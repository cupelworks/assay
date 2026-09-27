from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import TargetSettingsRead, TargetSettingsUpdate
from assay.services import (
    get_target_settings,
    reset_target_settings,
    update_target_settings,
)

router = APIRouter(tags=["settings"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_SETTINGS_FROM_DATABASE = {
    "source": "database",
    "updated_at": "2026-09-27T15:10:00+02:00",
    "url": "https://support-bot.internal/chat",
    "method": "POST",
    "headers": {"Authorization": "Bearer ${ASSAY_TARGET_API_KEY}"},
    "body": {"messages": [{"role": "user", "content": "{{input}}"}]},
    "output_path": "$.choices[0].message.content",
    "timeout_seconds": 30,
    "max_retries": 2,
}
_SETTINGS_FROM_ENVIRONMENT = {
    "source": "environment",
    "updated_at": None,
    "url": None,
    "method": "POST",
    "headers": {},
    "body": {"input": "{{input}}"},
    "output_path": "$.output",
    "timeout_seconds": 60,
    "max_retries": 2,
}
_SETTINGS_RESPONSE = {
    "description": (
        "The application-under-test settings now in effect. `source` says where they "
        "come from, for the whole group: `database` (saved from the UI) or "
        "`environment` (never saved, or reset — the `ASSAY_TARGET_*` variables and "
        "the defaults). Header values are exactly as stored: a `${NAME}` reference "
        "is never expanded."
    ),
    "content": {
        "application/json": {
            "examples": {
                "saved": {"summary": "Saved from the UI", "value": _SETTINGS_FROM_DATABASE},
                "environment": {"summary": "From the environment",
                                "value": _SETTINGS_FROM_ENVIRONMENT},
            }
        }
    },
}
_INVALID_SETTINGS = {
    "description": (
        "The resulting settings break at least one rule; every problem is listed, "
        "each `loc` pointing at the field. Nothing is saved."
    ),
    "content": {
        "application/json": {
            "example": {
                "detail": [
                    {"type": "value_error", "loc": ["body", "url"],
                     "msg": "must be an http:// or https:// URL"},
                    {"type": "less_than_equal", "loc": ["body", "max_retries"],
                     "msg": "Input should be less than or equal to 10"},
                ]
            }
        }
    },
}


@router.get(
    path="/settings/target",
    summary="Read the application-under-test settings",
    responses={200: _SETTINGS_RESPONSE},
)
async def read_target_settings(session: SessionDep) -> TargetSettingsRead:  # pragma: no cover
    """The settings Assay uses to call the application under test — the
    endpoint that answers when a test has no recorded `model_output` — and
    where they come from.

    With nothing saved from the UI, they come from the `ASSAY_TARGET_*`
    environment variables and the defaults (`source: environment`). Once
    saved, the saved settings apply as a whole and the environment no longer
    does (`source: database`) until they're reset with `DELETE`.

    Secrets are never returned: a header value holds a `${NAME}` reference to
    an environment variable of the worker, returned as written; whether that
    variable is actually set can only be known where it's resolved.
    """
    return await get_target_settings(session)


@router.patch(
    path="/settings/target",
    summary="Change the application-under-test settings",
    responses={
        200: {**_SETTINGS_RESPONSE, "description": "The saved settings, `source: database`."},
        422: _INVALID_SETTINGS,
    },
)
async def patch_target_settings(
        request: TargetSettingsUpdate,
        session: SessionDep,
) -> TargetSettingsRead:  # pragma: no cover
    """Change some settings; send only the fields to change.

    The fields sent are applied to the settings in effect and the result is
    validated as a whole before anything is saved:

    | Field | Rule |
    |---|---|
    | `url` | `http`/`https` URL, or `null` to unset it (see below) |
    | `method` | `POST`, `PUT` or `PATCH` |
    | `headers` | valid header names; every `${NAME}` a valid variable name |
    | `body` | a JSON object with `{{input}}` in at least one string value |
    | `output_path` | valid JSONPath |
    | `timeout_seconds` | above 0, at most 600 |
    | `max_retries` | 0 to 10 |

    With `url` unset, a run of a test without a recorded answer ends NotRan.
    `headers` and `body` replace the current ones entirely — send `{}` to
    remove every header. The first save copies the environment's settings
    forward with the changes applied; from then on the saved settings are
    the whole truth. A change applies from the next run that calls the
    application; the worker doesn't need a restart.
    """
    return await update_target_settings(request, session)


@router.delete(
    path="/settings/target",
    summary="Reset the application-under-test settings to the environment's",
    responses={200: {**_SETTINGS_RESPONSE,
                     "description": "The settings now in effect, `source: environment`."}},
)
async def delete_target_settings(session: SessionDep) -> TargetSettingsRead:  # pragma: no cover
    """Drop the settings saved from the UI, so the `ASSAY_TARGET_*`
    environment variables and the defaults apply again. Resetting when
    nothing is saved is not an error. Returns the settings now in effect, so
    the form can refresh from this one call.
    """
    return await reset_target_settings(session)
