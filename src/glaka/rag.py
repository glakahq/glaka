"""Ingest a business's documents and search them. Every query is scoped by business_id."""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from glaka.chunking import split_into_chunks
from glaka.embeddings import Embedder
from glaka.models import Chunk, Document


@dataclass(frozen=True)
class SearchHit:
    chunk_id: uuid.UUID
    source: str
    title: str
    content: str
    distance: float  # cosine distance: 0 = same meaning, 2 = opposite


def ingest_document(
    session: Session,
    embedder: Embedder,
    business_id: uuid.UUID,
    source: str,
    title: str,
    text: str,
) -> Document:
    """Store a document and its embedded chunks. Re-ingesting a source replaces it."""
    session.execute(
        delete(Document).where(Document.business_id == business_id, Document.source == source)
    )
    pieces = split_into_chunks(text)
    vectors = embedder.embed_documents(pieces)
    document = Document(business_id=business_id, source=source, title=title)
    document.chunks = [
        Chunk(business_id=business_id, position=i, content=piece, embedding=vector)
        for i, (piece, vector) in enumerate(zip(pieces, vectors, strict=True))
    ]
    session.add(document)
    session.flush()
    return document


def search(
    session: Session,
    embedder: Embedder,
    business_id: uuid.UUID,
    query: str,
    limit: int = 5,
) -> list[SearchHit]:
    """Return the chunks of THIS business closest in meaning to the query."""
    query_vector = embedder.embed_query(query)
    distance = Chunk.embedding.cosine_distance(query_vector).label("distance")
    rows = session.execute(
        select(Chunk, Document, distance)
        .join(Document)
        .where(Chunk.business_id == business_id)
        .order_by(distance)
        .limit(limit)
    ).all()
    return [
        SearchHit(
            chunk_id=chunk.id,
            source=document.source,
            title=document.title,
            content=chunk.content,
            distance=float(dist),
        )
        for chunk, document, dist in rows
    ]
