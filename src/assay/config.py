# pydantic-settings extends pydantic for app configuration: reads values from
# environment variables and .env files, then validates and coerces their types.
# Distinct from pydantic's use in schemas.py, which validates API request/response bodies.
import logging
from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

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
