from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables (and .env in dev)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://glaka:glaka@localhost:5433/glaka"


@lru_cache
def get_settings() -> Settings:
    return Settings()
