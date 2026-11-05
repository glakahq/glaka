"""FastAPI dependencies: what endpoints get injected (like Spring beans).

Tests replace these with fakes via app.dependency_overrides.
"""

from collections.abc import Iterator

from sqlalchemy.orm import Session

from glaka.db import SessionLocal
from glaka.embeddings import Embedder, OllamaEmbedder
from glaka.llm import ChatModel, make_chat_model


def get_session() -> Iterator[Session]:
    """One transaction per request: commit on success, roll back on error."""
    with SessionLocal.begin() as session:
        yield session


def get_embedder() -> Embedder:
    return OllamaEmbedder()


def get_llm() -> ChatModel:
    return make_chat_model()
