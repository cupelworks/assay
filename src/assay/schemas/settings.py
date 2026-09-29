"""Settings users see and change from the UI.

TargetSettings is the one definition of the application-under-test
settings: their shape, defaults and rules. It validates the environment
fallback (config.py), what PATCH /settings/target saves, a check's
proposed settings, and a stored row read back — so a value refused in one
place can't get in through another.
"""
import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from jsonpath_ng import parse as parse_jsonpath
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from assay.messages import sentence
from assay.models import TargetCheckStatus

INPUT_PLACEHOLDER = "{{input}}"
ALLOWED_METHODS = ("POST", "PUT", "PATCH")
MAX_TIMEOUT_SECONDS = 600
MAX_RETRIES = 10

# RFC 9110 token characters — what a header name may be made of
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_REFERENCE = re.compile(r"\$\{([^}]*)\}")
_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SettingsSource(StrEnum):
    """Where a group's effective settings come from: its row in the
    settings table, or — when it has none — the ASSAY_* environment and the
    code defaults."""
    database = "database"
    environment = "environment"


class TargetSettings(BaseModel):
    """The application under test: how Assay calls it and where the answer
    is in its reply.

    Header values may reference environment variables as ${NAME}; the
    reference is stored and returned as written and only resolved by the
    process making the call, so a secret never passes through here.
    """
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={
            "example": {
                "url": "https://support-bot.internal/chat",
                "method": "POST",
                "headers": {"Authorization": "Bearer ${ASSAY_TARGET_API_KEY}"},
                "body": {"input": "{{input}}"},
                "output_path": "$.output",
                "timeout_seconds": 60,
                "max_retries": 2,
            }
        },
    )

    url: str | None = Field(
        None,
        description="The application's endpoint, `http` or `https`. Null means no "
                    "application is configured: a run of a test without a recorded "
                    "answer then ends NotRan.",
    )
    method: str = Field(
        "POST",
        description=f"HTTP method: one of {', '.join(ALLOWED_METHODS)} (the request "
                    "always carries a JSON body). Stored uppercased.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Headers sent with every call. A value may reference an "
                    "environment variable of the calling process as `${NAME}` — "
                    "put secrets there, never here: the value is stored and "
                    "returned exactly as written.",
    )
    body: dict[str, Any] = Field(
        default_factory=lambda: {"input": INPUT_PLACEHOLDER},
        description="JSON body template. Every `{{input}}` inside a string value "
                    "is replaced by the test's input; at least one is required.",
    )
    output_path: str = Field(
        "$.output",
        description="JSONPath to the answer in the application's JSON reply.",
    )
    timeout_seconds: float = Field(
        60, gt=0, le=MAX_TIMEOUT_SECONDS,
        description=f"Per-call timeout, above 0 and at most {MAX_TIMEOUT_SECONDS}.",
    )
    max_retries: int = Field(
        2, ge=0, le=MAX_RETRIES,
        description="Retries after a connection error, timeout, 5xx or 429 "
                    f"(0 to {MAX_RETRIES}); 0 means a single call. Other "
                    "failures are never retried.",
    )

    @field_validator("url", mode="before")
    @classmethod
    def _blank_url_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Must be an http:// or https:// URL")
        return value

    @field_validator("method")
    @classmethod
    def _known_method(cls, value: str) -> str:
        method = value.strip().upper()
        if method not in ALLOWED_METHODS:
            raise ValueError(f"Must be one of {', '.join(ALLOWED_METHODS)}")
        return method

    @field_validator("headers")
    @classmethod
    def _valid_headers(cls, value: dict[str, str]) -> dict[str, str]:
        problems = []
        for name, header_value in value.items():
            if not _HEADER_NAME.match(name):
                problems.append(f"{name!r} is not a valid header name")
            if "\r" in header_value or "\n" in header_value:
                problems.append(f"the value of {name!r} contains a line break")
            for reference in _REFERENCE.findall(header_value):
                if not _VARIABLE_NAME.match(reference):
                    problems.append(
                        f"the value of {name!r} references ${{{reference}}}, which is "
                        "not a valid variable name (letters, digits and _, not "
                        "starting with a digit)"
                    )
        if problems:
            raise ValueError(sentence("; ".join(problems)))
        return value

    @field_validator("body")
    @classmethod
    def _body_takes_the_input(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not _contains_placeholder(value):
            raise ValueError(
                f"Must contain {INPUT_PLACEHOLDER} in at least one string value, "
                "or the application never receives the test's input"
            )
        return value

    @field_validator("output_path")
    @classmethod
    def _valid_jsonpath(cls, value: str) -> str:
        try:
            parse_jsonpath(value)
        except Exception as exc:  # jsonpath-ng's parser raises assorted exception types
            raise ValueError(f"Not a valid JSONPath: {exc}") from None
        return value


class TargetSettingsUpdate(BaseModel):
    """PATCH /settings/target: only the fields sent change. The result —
    the current settings with these applied — is validated as a whole
    against TargetSettings' rules. `headers` and `body` replace the stored
    ones entirely; `url: null` unsets the URL."""
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"url": "https://support-bot.internal/chat",
                                       "timeout_seconds": 30}},
    )

    url: str | None = None
    method: str | None = None
    headers: dict[str, str] | None = None
    body: dict[str, Any] | None = None
    output_path: str | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None


class TargetSettingsRead(TargetSettings):
    """The effective application-under-test settings and where they come from."""
    source: SettingsSource = Field(
        ...,
        description="`database`: saved from the UI. `environment`: never saved (or "
                    "reset), so read from the ASSAY_TARGET_* environment variables "
                    "and the defaults. The source applies to the whole group.",
    )
    updated_at: datetime | None = Field(
        None,
        description="When the settings were last saved from the UI; null when the "
                    "source is the environment.",
    )


class TargetCheckRequest(BaseModel):
    """POST /settings/target/checks."""
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "input": "What time does the store open on Saturdays?",
                "settings": {"output_path": "$.choices[0].message.content"},
            }
        },
    )

    input: str = Field(
        ..., min_length=1,
        description="The text sent to the application as `{{input}}`.",
    )
    settings: TargetSettingsUpdate | None = Field(
        None,
        description="Optional fields to try on top of the current settings, validated "
                    "like a PATCH. Never saved.",
    )


class TargetCheck(BaseModel):
    """One check of the application-under-test settings, run on a worker."""
    id: uuid.UUID
    status: TargetCheckStatus = Field(
        ...,
        description="`pending` until a worker picks it up, `running` while it calls "
                    "the application, `completed` once the outcome is written.",
    )
    created_at: datetime
    completed_at: datetime | None = None
    settings: TargetSettings = Field(
        ..., description="The complete settings this check uses (or used).",
    )
    ok: bool | None = Field(
        None,
        description="Whether the application gave a usable answer. Null until completed.",
    )
    status_code: int | None = Field(
        None, description="The application's HTTP status, when a response came back.",
    )
    latency_ms: float | None = Field(
        None, description="How long the call took, when a response came back.",
    )
    answer: str | None = Field(
        None, description="The answer found at the output path, when `ok`.",
    )
    error: str | None = Field(
        None,
        description="Why the check failed, when not `ok`: the reason a run would get "
                    "for a failed call, or that the answer at the output path was "
                    "empty (a run scores an empty answer; a check reports it).",
    )


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return INPUT_PLACEHOLDER in value
    if isinstance(value, dict):
        return any(_contains_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_placeholder(item) for item in value)
    return False


def settings_errors(exc: ValidationError) -> list[dict]:
    """Every problem in exc, as FastAPI's own 422 items (`type`, `loc`,
    `msg`) minus the submitted values, with pydantic's "Value error, "
    prefix dropped from the rules' own messages."""
    return [
        {**error, "msg": error["msg"].removeprefix("Value error, ")}
        for error in exc.errors(include_url=False, include_input=False,
                                include_context=False)
    ]


def describe_validation_error(exc: ValidationError, prefix: str = "") -> str:
    """One line naming every problem, field by field — for a log line, a
    run's error, or a start-up failure. Never includes the submitted values.
    With a prefix (e.g. "ASSAY_TARGET_"), field names are written as the
    environment variables they came from.
    """
    problems = []
    for error in settings_errors(exc):
        field = ".".join(str(part) for part in error["loc"])
        problems.append(f"{prefix}{field.upper() if prefix else field}: {error['msg']}")
    return "; ".join(problems)
