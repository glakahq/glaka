"""FastAPI dependencies: what endpoints get injected (like Spring beans).

Tests replace these with fakes via app.dependency_overrides.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from glaka.db import SessionLocal
from glaka.embeddings import Embedder, OllamaEmbedder
from glaka.inbox import Inbox
from glaka.llm import ChatModel, make_chat_model
from glaka.models import Business


def get_session() -> Iterator[Session]:
    """One transaction per request: commit on success, roll back on error."""
    with SessionLocal.begin() as session:
        yield session


def get_embedder() -> Embedder:
    return OllamaEmbedder()


def get_llm() -> ChatModel:
    return make_chat_model()


def get_inbox(request: Request) -> Inbox:
    """Built once at startup (see main.lifespan), like a singleton bean."""
    return request.app.state.inbox


def get_business(slug: str, session: Annotated[Session, Depends(get_session)]) -> Business:
    business = session.scalar(select(Business).where(Business.slug == slug))
    if business is None:
        raise HTTPException(status_code=404, detail="Business not found")
    return business
