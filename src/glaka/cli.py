"""Glaka command-line tools.

  uv run python -m glaka.cli ingest --business demo-bathrooms examples/demo-bathrooms/*.md
  uv run python -m glaka.cli search --business demo-bathrooms "How much is a shower install?"
  uv run python -m glaka.cli poll-email --business demo-bathrooms [--watch 60]
  uv run python -m glaka.cli verify-audit --business demo-bathrooms
  uv run python -m glaka.cli create-user --email you@example.com --name "You" [--admin]
  uv run python -m glaka.cli add-member --email you@example.com --business demo-bathrooms
  uv run python -m glaka.cli create-token --email you@example.com --name evals
  uv run python -m glaka.cli connect-gmail --business demo-bathrooms
"""

import argparse
import os
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

    user_cmd = sub.add_parser("create-user", help="create a dashboard account (asks for a password)")
    user_cmd.add_argument("--email", required=True)
    user_cmd.add_argument("--name", required=True)
    user_cmd.add_argument("--admin", action="store_true", help="can see and create all businesses")

    member_cmd = sub.add_parser("add-member", help="give a user access to a business")
    member_cmd.add_argument("--email", required=True)
    member_cmd.add_argument("--business", required=True, help="business slug")
    member_cmd.add_argument("--role", default="owner", choices=["owner", "staff"])

    token_cmd = sub.add_parser("create-token", help="create an API token (for scripts and evals)")
    token_cmd.add_argument("--email", required=True)
    token_cmd.add_argument("--name", required=True, help="what it's for, e.g. evals")

    gmail_cmd = sub.add_parser("connect-gmail", help="sign in to Gmail for a business (opens a browser)")
    gmail_cmd.add_argument("--business", required=True, help="business slug")

    args = parser.parse_args()
    if args.command in {"create-user", "add-member", "create-token", "connect-gmail"}:
        _accounts(args)
        return
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


def _user(session: Session, email: str):
    from glaka.models import User

    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        sys.exit(f"No user {email!r}. Create one with create-user.")
    return user


def _accounts(args: argparse.Namespace) -> None:
    import getpass

    from glaka.auth import add_member, create_api_token, create_user

    if args.command == "connect-gmail":
        from glaka.config import get_settings
        from glaka.gmail_mcp.server import authorize

        with SessionLocal() as session:
            business = _business(session, args.business)
            print(f"Connecting Gmail for {business.name}. Sign in with the account that receives its enquiries.")
            authorize(get_settings().gmail_token_file(business.id))
        return

    with SessionLocal.begin() as session:
        if args.command == "create-user":
            password = os.environ.get("GLAKA_PASSWORD") or getpass.getpass("Password (10+ characters): ")
            try:
                user = create_user(session, args.email, args.name, password, is_admin=args.admin)
            except ValueError as exc:
                sys.exit(str(exc))
            print(f"Created {user.email}{' (admin)' if user.is_admin else ''}")
        elif args.command == "add-member":
            add_member(session, _user(session, args.email), _business(session, args.business), args.role)
            print(f"{args.email} can now see {args.business} ({args.role})")
        elif args.command == "create-token":
            token = create_api_token(session, _user(session, args.email), args.name)
            print("API token (shown once; store it somewhere safe):")
            print(token)


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
