"""FastAPI dependencies: what endpoints get injected (like Spring beans).

Tests replace these with fakes via app.dependency_overrides.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from glaka.auth import can_access, load_user
from glaka.db import SessionLocal
from glaka.embeddings import Embedder, OllamaEmbedder
from glaka.inbox import Inbox
from glaka.llm import ChatModel, make_chat_model
from glaka.models import Business, User


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


def current_user(request: Request, session: Annotated[Session, Depends(get_session)]) -> User:
    """The signed-in user (cookie) or token owner. Raises NotAuthenticated otherwise."""
    return load_user(request, session)


def get_business(
    slug: str,
    session: Annotated[Session, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> Business:
    """The business in the URL, if this user may see it. Otherwise 404, not 403, so
    other businesses' existence isn't leaked."""
    business = session.scalar(select(Business).where(Business.slug == slug))
    if business is None or not can_access(session, user, business):
        raise HTTPException(status_code=404, detail="Business not found")
    return business
