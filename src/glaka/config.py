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

    # Gmail. Files live in .secrets/ (gitignored): one OAuth client for the app, and one
    # token per business in gmail_token_dir/<business id>.json.
    gmail_client_file: Path = Path(".secrets/gmail-oauth-client.json")
    gmail_token_dir: Path = Path(".secrets/gmail")
    # Check connected mailboxes every N seconds in the background (0 = off; use the button).
    email_poll_seconds: int = 60

    # Web sessions. Set SECRET_KEY in production; without it a random key is used and
    # everyone is signed out when the app restarts.
    secret_key: SecretStr | None = None
    cookie_secure: bool = False  # True behind HTTPS

    def gmail_token_file(self, business_id: object) -> Path:
        return self.gmail_token_dir / f"{business_id}.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
