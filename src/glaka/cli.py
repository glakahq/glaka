"""Command-line tools for step 2.

  uv run python -m glaka.cli ingest --business demo-bathrooms examples/demo-bathrooms/*.md
  uv run python -m glaka.cli search --business demo-bathrooms "How much is a shower install?"
"""

import argparse
import sys
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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_cmd = sub.add_parser("ingest", help="embed and store documents")
    ingest_cmd.add_argument("--business", required=True, help="business slug")
    ingest_cmd.add_argument("files", nargs="+", type=Path)

    search_cmd = sub.add_parser("search", help="find the most relevant chunks")
    search_cmd.add_argument("--business", required=True, help="business slug")
    search_cmd.add_argument("query")
    search_cmd.add_argument("--limit", type=int, default=3)

    args = parser.parse_args()
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


if __name__ == "__main__":
    main()
