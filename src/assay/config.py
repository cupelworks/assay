# pydantic-settings extends pydantic for app configuration: reads values from
# environment variables and .env files, then validates and coerces their types.
# Distinct from pydantic's use in schemas.py, which validates API request/response bodies.
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


# Module-level singleton — imported across the app, read once at startup.
settings = Settings()
