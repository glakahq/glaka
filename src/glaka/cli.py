"""Command-line tools for step 2.

  uv run python -m glaka.cli ingest --business demo-bathrooms examples/demo-bathrooms/*.md
  uv run python -m glaka.cli search --business demo-bathrooms "How much is a shower install?"
  uv run python -m glaka.cli poll-email --business demo-bathrooms [--watch 60]
  uv run python -m glaka.cli verify-audit --business demo-bathrooms
"""

import argparse
import sys
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from glaka.db import SessionLocal
from glaka.embeddings import OllamaEmbedder
from glaka.models import Business
from glaka.rag import ingest_document, search


def _business(session: Session, slug: str) -> Business:
    business = session.scalar(select(Business).where(Business.slug == slug))
    if business is None:
        sys.exit(f"No business with slug {slug!r}. Create it with: python -m glaka.seed")
    return business


def _title(path: Path, text: str) -> str:
    first = text.lstrip().splitlines()[0] if text.strip() else ""
    return first.lstrip("# ").strip() if first.startswith("#") else path.stem


def main() -> None:
    from glaka.logs import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_cmd = sub.add_parser("ingest", help="embed and store documents")
    ingest_cmd.add_argument("--business", required=True, help="business slug")
    ingest_cmd.add_argument("files", nargs="+", type=Path)

    search_cmd = sub.add_parser("search", help="find the most relevant chunks")
    search_cmd.add_argument("--business", required=True, help="business slug")
    search_cmd.add_argument("query")
    search_cmd.add_argument("--limit", type=int, default=3)

    poll_cmd = sub.add_parser("poll-email", help="turn new labelled emails into enquiries")
    poll_cmd.add_argument("--business", required=True, help="business slug")
    poll_cmd.add_argument("--watch", type=int, metavar="SECONDS", help="keep polling every N seconds")

    audit_cmd = sub.add_parser("verify-audit", help="check the audit trail hasn't been tampered with")
    audit_cmd.add_argument("--business", required=True, help="business slug")

    args = parser.parse_args()
    if args.command == "verify-audit":
        from glaka.audit import verify_chain

        with SessionLocal() as session:
            check = verify_chain(session, _business(session, args.business).id)
        if check.ok:
            print(f"OK: {check.events} events, chain intact")
        else:
            sys.exit(f"TAMPERED at event {check.first_bad_id}: {check.reason}")
        return
    if args.command == "poll-email":
        _poll_email(args.business, args.watch)
        return
    embedder = OllamaEmbedder()

    with SessionLocal.begin() as session:
        business = _business(session, args.business)
        if args.command == "ingest":
            for path in args.files:
                text = path.read_text()
                doc = ingest_document(session, embedder, business.id, path.name, _title(path, text), text)
                print(f"{path.name}: {len(doc.chunks)} chunks")
        else:
            for hit in search(session, embedder, business.id, args.query, args.limit):
                print(f"\n[{hit.source}] distance={hit.distance:.3f}\n{hit.content}")


def _poll_email(slug: str, watch: int | None) -> None:
    from glaka.mail import MailError
    from glaka.runtime import open_inbox

    with SessionLocal() as session:
        business_id = _business(session, slug).id
    with open_inbox() as inbox:
        while True:
            try:
                r = inbox.poll_email(business_id)
                print(f"{time.strftime('%H:%M:%S')} new={r.new} already_seen={r.already_seen} failed={r.failed}")
            except MailError as exc:
                print(f"{time.strftime('%H:%M:%S')} email check failed: {exc}", file=sys.stderr)
            if not watch:
                return
            time.sleep(watch)


if __name__ == "__main__":
    main()
