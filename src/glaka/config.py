from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables (and .env in dev)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://glaka:glaka@localhost:5433/glaka"

    # Embeddings: local Ollama (small model, runs fine on a laptop).
    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"

    # Chat model: OpenRouter (hosted) by default, or Ollama (local).
    llm_provider: Literal["openrouter", "ollama"] = "openrouter"
    chat_model: str = "anthropic/claude-haiku-5.5"
    openrouter_api_key: SecretStr | None = None
    openrouter_url: str = "https://openrouter.ai/api/v1"
    # Free (":free") models need this, because their hosts may train on prompts.
    # Only enable it with demo data, never with real customer messages.
    openrouter_allow_data_collection: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
