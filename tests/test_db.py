"""Integration tests: need the Docker Postgres running (docker compose up -d)."""

from sqlalchemy import text

from glaka.seed import seed_business


def test_pgvector_extension_is_installed(session) -> None:
    assert session.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")) == 1


def test_seed_business_is_idempotent(session) -> None:
    first = seed_business(session, "test-tenant", "Test Tenant")
    second = seed_business(session, "test-tenant", "Different Name")

    assert first.id == second.id
    assert second.name == "Test Tenant"
