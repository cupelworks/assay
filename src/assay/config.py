# pydantic-settings extends pydantic for app configuration: reads values from
# environment variables and .env files, then validates and coerces their types.
# Distinct from pydantic's use in schemas.py, which validates API request/response bodies.
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASSAY_",  # e.g. ASSAY_HOST overrides host
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = 8000
    # sqlite+aiosqlite locally; postgresql+asyncpg in production
    database_url: str = "sqlite+aiosqlite:///./assay.db"
    # comma-separated in the env, e.g. ASSAY_CORS_ALLOWED_ORIGINS=http://localhost:3000,https://app.example.com
    cors_allowed_origins: list[str] = ["http://localhost:4200"]

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


# Module-level singleton — imported across the app, read once at startup.
settings = Settings()
