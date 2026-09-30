# pydantic-settings extends pydantic for app configuration: reads values from
# environment variables and .env files, then validates and coerces their types.
# Distinct from pydantic's use in schemas.py, which validates API request/response bodies.
import logging
import os
from pathlib import Path
from typing import Annotated, Literal

from dotenv import dotenv_values
from pydantic import ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from assay.schemas.settings import JudgeSettings, TargetSettings, describe_validation_error

# Anchored to this file's own location, not the process's working directory:
# a bare "./.env" is resolved relative to CWD, which silently finds nothing
# (pydantic-settings treats a missing env_file as "no overrides", not an
# error) the moment something runs from anywhere but the project root —
# every *_url default below then falls back to its own relative-path form,
# for the exact same reason. Three parents: config.py -> assay/ -> src/ -> root.
_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASSAY_",  # e.g. ASSAY_HOST overrides host
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Level for the app's own loggers (the `assay` namespace); third-party
    # loggers stay at INFO regardless. DEBUG additionally logs every SQL
    # statement (sqlalchemy.engine) — see assay/logging_config.py.
    log_level: str = "INFO"
    # text: one human-readable line per record, for a terminal. json: one JSON
    # object per line, for a log collector (Azure Monitor, Datadog, ...) that
    # indexes the fields instead of grepping the text.
    log_format: Literal["text", "json"] = "text"
    host: str = "127.0.0.1"
    port: int = 8000
    # sqlite+aiosqlite locally; postgresql+asyncpg in production
    database_url: str = "sqlite+aiosqlite:///./assay.db"
    # comma-separated in the env, e.g. ASSAY_CORS_ALLOWED_ORIGINS=http://localhost:3000,https://app.example.com
    # NoDecode: pydantic-settings otherwise tries to JSON-decode any list-typed
    # field read from the environment/.env before _split_cors_origins ever
    # runs, and a plain comma-separated string isn't valid JSON — raises
    # SettingsError instead of falling back to the string validator below.
    cors_allowed_origins: Annotated[list[str], NoDecode] = ["http://localhost:4200"]
    # Celery's broker (task queue) and result backend. Only read by the worker
    # process (assay.worker) — the API process never imports celery and works
    # fine with these unset/unreachable. Local default is Redis (brew install
    # redis && brew services start redis on macOS); swap to a managed
    # rediss:// instance in production (see README) by overriding the env
    # vars, no code change. A SQLite-backed alternative needing no separate
    # service at all is documented in README/.env.example for anyone who'd
    # rather not install Redis locally.
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"
    # assay/worker/db.py's sync engine pool — only read by the worker process.
    # Right sizing depends on the worker's own --pool choice and --concurrency
    # (see worker/db.py's module docstring), both operational choices made at
    # deploy/run time, not at code-change time — so these are env-overridable
    # rather than hardcoded, the same reasoning as database_url itself: change
    # the number for a given environment without a new release to do it.
    worker_db_pool_size: int = 10
    worker_db_max_overflow: int = 10
    # Celery Beat's reconciliation scan (worker/tasks/reconcile_runs.py) — how
    # often it runs, and how old a Pending run must be before the scan treats its
    # original dispatch as lost and re-publishes it. Only read by the worker/Beat.
    reconciliation_interval_minutes: int = 60
    reconciliation_pending_threshold_minutes: int = 15
    # The application under test — the fallback for when no settings have
    # been saved from the UI (a saved row wins for the whole group),
    # validated by the same rules as a save (_validate_target). One
    # deployment tests one application; unset URL means none is configured,
    # and such a run lands on NotRan saying so. The request is described as data rather
    # than coded per application: the body is a JSON template whose string
    # values may contain {{input}}, the answer is read at a JSONPath, and
    # header values may reference ${ENV_VAR} so secrets stay out of .env.
    # The defaults are Assay's own minimal contract: POST {"input": ...},
    # answer at $.output.
    target_url: str | None = None
    target_method: str = "POST"
    target_headers: dict[str, str] = {}
    target_body: dict = {"input": "{{input}}"}
    target_output_path: str = "$.output"
    target_timeout_seconds: float = 60
    # Retries only for failures that say "try again" (connection error,
    # timeout, 5xx, 429) — a retry is a second call to the application, which
    # can cost money or have side effects, so 0 is a valid choice.
    target_max_retries: int = 2
    # The LLM judge's model — likewise the fallback for when no judge
    # settings have been saved from the UI, validated by the same rules as a
    # save (_validate_judge). No provider means no judge is configured: a
    # judge check in a run fails, saying so. The key itself is never a
    # setting, only the name of the variable holding it (null: the
    # provider's standard ANTHROPIC_API_KEY / OPENAI_API_KEY), read by the
    # worker when it calls.
    judge_provider: str | None = None
    judge_model: str | None = None
    judge_url: str | None = None
    judge_api_key_env: str | None = None
    judge_timeout_seconds: float = 60
    judge_max_retries: int = 2

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("target_method")
    @classmethod
    def _upper_method(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _validate_target(self) -> "Settings":
        # The same rules as a save from the UI, so a value PATCH
        # /settings/target would refuse can't get in
        # through the environment instead — and it fails here, at start-up.
        try:
            self.target_settings()
        except ValidationError as exc:
            raise ValueError(describe_validation_error(exc, prefix="ASSAY_TARGET_")) from None
        return self

    @model_validator(mode="after")
    def _validate_judge(self) -> "Settings":
        # As _validate_target, for the judge's group
        try:
            self.judge_settings()
        except ValidationError as exc:
            raise ValueError(describe_validation_error(exc, prefix="ASSAY_JUDGE_")) from None
        return self

    def judge_settings(self) -> "JudgeSettings":
        """The judge settings as this environment defines them — the
        fallback whenever none have been saved from the UI."""
        return JudgeSettings(
            provider=self.judge_provider or None,
            model=self.judge_model,
            url=self.judge_url,
            api_key_env=self.judge_api_key_env,
            timeout_seconds=self.judge_timeout_seconds,
            max_retries=self.judge_max_retries,
        )

    def target_settings(self) -> "TargetSettings":
        """The application-under-test settings as this environment defines
        them — the fallback whenever none have been saved from the UI."""
        return TargetSettings(
            url=self.target_url,
            method=self.target_method,
            headers=self.target_headers,
            body=self.target_body,
            output_path=self.target_output_path,
            timeout_seconds=self.target_timeout_seconds,
            max_retries=self.target_max_retries,
        )

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in logging.getLevelNamesMapping():
            raise ValueError(
                f"unknown log level {value!r}; expected one of "
                f"{', '.join(logging.getLevelNamesMapping())}"
            )
        return level


# Module-level singleton — imported across the app, read once at startup.
settings = Settings()


def environment_value(name: str) -> str | None:
    """A variable from this process's environment, else from the project's
    .env file — where the ASSAY_* settings come from too, but pydantic-settings
    reads them without exporting anything. Secrets referenced by name (the
    judge's API key, a ${NAME} in the application's headers) are looked up
    here by the process that uses them, when it does: a change to .env
    applies from the next call."""
    value = os.environ.get(name)
    if value is None and _ENV_FILE.is_file():
        value = dotenv_values(_ENV_FILE).get(name)
    return value
