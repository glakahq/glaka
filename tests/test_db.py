"""Integration tests: need the Docker Postgres running (docker compose up -d)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from glaka.db import engine, SessionLocal
from glaka.seed import seed_business


@pytest.fixture
def session():
    """A session whose changes are rolled back after each test."""
    try:
        connection = engine.connect()
    except OperationalError:
        pytest.skip("Postgres not running (docker compose up -d)")
    transaction = connection.begin()
    session = SessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


def test_pgvector_extension_is_installed(session) -> None:
    assert session.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")) == 1


def test_seed_business_is_idempotent(session) -> None:
    first = seed_business(session, "test-tenant", "Test Tenant")
    second = seed_business(session, "test-tenant", "Different Name")

    assert first.id == second.id
    assert second.name == "Test Tenant"
