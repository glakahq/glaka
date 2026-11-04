"""Create a business (tenant) if it doesn't exist yet.

Usage: uv run python -m glaka.seed --slug acme --name "Acme Ltd"
"""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from glaka.db import SessionLocal
from glaka.models import Business


def seed_business(session: Session, slug: str, name: str) -> Business:
    """Idempotent: returns the existing business if the slug is taken."""
    existing = session.scalar(select(Business).where(Business.slug == slug))
    if existing:
        return existing
    business = Business(slug=slug, name=name)
    session.add(business)
    session.flush()
    return business


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    with SessionLocal.begin() as session:
        business = seed_business(session, args.slug, args.name)
        print(f"{business.slug}: {business.id}")


if __name__ == "__main__":
    main()
