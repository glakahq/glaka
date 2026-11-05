"""Shared pytest fixtures (pytest loads conftest.py automatically)."""

from contextlib import contextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.exc import OperationalError

from glaka.answer import LlmAnswer
from glaka.db import SessionLocal, engine
from glaka.inbox import Inbox
from glaka.rag import ingest_document
from glaka.seed import seed_business
from glaka.workflow import build_workflow
from tests.fakes import FakeChat, FakeEmbedder, FakeMail

PRICES = "# Prices\n\nshower install costs £450"


@pytest.fixture
def session():
    """A DB session whose changes are rolled back after each test."""
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("Postgres not running (docker compose up -d)")
    transaction = connection.begin()
    session = SessionLocal(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def session_scope(session):
    """Stands in for SessionLocal.begin: every 'transaction' reuses the test session."""

    @contextmanager
    def scope():
        yield session
        session.flush()

    return scope


@pytest.fixture
def llm():
    return FakeChat(LlmAnswer(can_answer=True, answer="It costs £450.", cited_sources=[1]))


@pytest.fixture
def shop(session):
    embedder = FakeEmbedder()
    shop = seed_business(session, "test-shop", "Test Shop")
    ingest_document(session, embedder, shop.id, "prices.md", "Prices", PRICES)
    return shop


@pytest.fixture
def mail():
    return FakeMail()


@pytest.fixture
def inbox(session_scope, llm, mail):
    """The real workflow, with fakes for AI and mail, and an in-memory checkpointer."""
    workflow = build_workflow(session_scope, FakeEmbedder, lambda: llm, lambda _business_id: mail, InMemorySaver())
    return Inbox(session_scope, workflow, lambda _business_id: mail)


PASSWORD = "correct horse battery"


@pytest.fixture
def owner(session, shop):
    """A signed-up user who is a member of `shop` (and nothing else)."""
    from glaka.auth import add_member, create_user

    user = create_user(session, "owner@example.com", "Olive Owner", PASSWORD)
    add_member(session, user, shop)
    return user


def csrf_from(page_html: str) -> str:
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', page_html)
    assert match, "page has no CSRF token"
    return match.group(1)


def sign_in(client, email: str = "owner@example.com", password: str = PASSWORD) -> str:
    """Sign in through the real login form. Returns the session's CSRF token."""
    token = csrf_from(client.get("/login").text)
    response = client.post("/login", data={"email": email, "password": password, "csrf_token": token},
                           follow_redirects=False)
    assert response.status_code == 303 and "error" not in response.headers["location"], response.headers
    return csrf_from(client.get("/ui").text)
