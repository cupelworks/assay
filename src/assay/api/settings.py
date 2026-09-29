import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from assay.db import get_session
from assay.schemas import TargetCheck, TargetCheckRequest, TargetSettingsRead, TargetSettingsUpdate
from assay.services import (
    create_target_check,
    get_target_check,
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
                     "msg": "Must be an http:// or https:// URL"},
                    {"type": "less_than_equal", "loc": ["body", "max_retries"],
                     "msg": "Input should be less than or equal to 10"},
                ]
            }
        }
    },
}
_CHECK_PENDING = {
    "id": "5b0c9a3e-2f1d-4c1e-9d6b-0f4a1f2e7c11",
    "status": "pending",
    "created_at": "2026-09-27T15:12:03+02:00",
    "completed_at": None,
    "settings": {k: v for k, v in _SETTINGS_FROM_DATABASE.items()
                 if k not in ("source", "updated_at")},
    "ok": None, "status_code": None, "latency_ms": None, "answer": None, "error": None,
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
    an environment variable of the worker, returned as written. Whether that
    variable is actually set can only be known where it's resolved — run a
    check (`POST /settings/target/checks`).
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


@router.post(
    path="/settings/target/checks",
    summary="Check the application-under-test settings on a worker",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {
            "description": (
                "The check, `pending` until a worker has made the call — poll "
                "`GET /settings/target/checks/{check_id}`. Already `completed` "
                "with `ok: false` if it couldn't be sent to a worker."
            ),
            "content": {"application/json": {"example": _CHECK_PENDING}},
        },
        422: {**_INVALID_SETTINGS,
              "description": "The proposed settings break at least one rule. No check "
                             "is created."},
    },
)
async def post_target_check(
        request: TargetCheckRequest,
        session: SessionDep,
) -> TargetCheck:  # pragma: no cover
    """Ask a worker to call the application once with `input`, and record
    what happened.

    The check runs on a worker because that's where runs call the
    application: same network, same environment — the only place a `${NAME}`
    in a header can be resolved. It needs Redis and a running worker.

    `settings` optionally proposes fields to try on top of the settings in
    effect, validated like a PATCH; they're stored on the check and never
    saved as the settings, so a change can be checked before saving it.

    One attempt, no retries, with the configured timeout. A check still
    queued after 5 minutes (no worker running) is completed without calling
    the application, with the reason.
    """
    return await create_target_check(request, session)


@router.get(
    path="/settings/target/checks/{check_id}",
    summary="Read a check of the application-under-test settings",
    responses={
        200: {
            "description": (
                "The check as far as it has got. Poll until `status` is `completed`, "
                "then `ok` says whether a usable answer came back: `answer` holds it, "
                "or `error` says why not — the reason a run would get for a failed "
                "call, or that the answer was empty, which a run scores but a check "
                "reports. "
                "`status_code` and `latency_ms` are set whenever a response came back."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "ok": {"summary": "The application answered", "value": {
                            **_CHECK_PENDING, "status": "completed",
                            "completed_at": "2026-09-27T15:12:04+02:00", "ok": True,
                            "status_code": 200, "latency_ms": 412.5,
                            "answer": "We open at 9am on Saturdays.",
                        }},
                        "failed": {"summary": "The application refused the key", "value": {
                            **_CHECK_PENDING, "status": "completed",
                            "completed_at": "2026-09-27T15:12:04+02:00", "ok": False,
                            "status_code": 401, "latency_ms": 88.1,
                            "error": "Application answered HTTP 401",
                        }},
                        "pending": {"summary": "Not picked up yet", "value": _CHECK_PENDING},
                    }
                }
            },
        },
        404: {
            "description": "No check exists with the given ID.",
            "content": {"application/json": {
                "example": {"detail": "Check with ID '<check_id>' not found"}
            }},
        },
    },
)
async def read_target_check(
        check_id: uuid.UUID,
        session: SessionDep,
) -> TargetCheck:  # pragma: no cover
    """One check, as far as it has got: `pending`, `running` (a worker is
    calling the application) or `completed` with its outcome."""
    return await get_target_check(check_id, session)
