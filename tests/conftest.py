"""Shared pytest fixtures (pytest loads conftest.py automatically)."""

import pytest
from sqlalchemy.exc import OperationalError

from glaka.db import SessionLocal, engine


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
