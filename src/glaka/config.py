from functools import lru_cache
from pathlib import Path
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
    # Chunks further than this (cosine distance) count as "not relevant". Measured for
    # nomic-embed-text on the demo data: on-topic <= 0.354, off-topic >= 0.396.
    # Re-measure if you change the embedding model.
    retrieval_max_distance: float = 0.38

    # Chat model: OpenRouter (hosted) by default, or Ollama (local).
    llm_provider: Literal["openrouter", "ollama"] = "openrouter"
    chat_model: str = "anthropic/claude-haiku-5.5"
    openrouter_api_key: SecretStr | None = None
    openrouter_url: str = "https://openrouter.ai/api/v1"
    # Free (":free") models need this, because their hosts may train on prompts.
    # Only enable it with demo data, never with real customer messages.
    openrouter_allow_data_collection: bool = False

    # Gmail (step 4). Files live in .secrets/ (gitignored).
    gmail_client_file: Path = Path(".secrets/gmail-oauth-client.json")
    gmail_token_file: Path = Path(".secrets/gmail-token.json")
    # Glaka only ever reads mail carrying this Gmail label (set by a Gmail filter).
    gmail_label: str = "glaka"


@lru_cache
def get_settings() -> Settings:
    return Settings()
