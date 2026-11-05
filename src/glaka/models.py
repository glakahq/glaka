import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# nomic-embed-text produces 768-dimensional vectors. Changing the embedding
# model to one with a different size needs a migration and a re-ingest.
EMBEDDING_DIM = 768


class Base(DeclarativeBase):
    pass


class Business(Base):
    """A tenant. Every other table carries a business_id pointing here."""

    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    # Opt-in: send grounded drafts without waiting for the owner. Off by default.
    auto_send: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Document(Base):
    """A source file the business gave us (price list, FAQ, services page...)."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("business_id", "source"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(500))  # e.g. "pricing.md"
    title: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    """A small piece of a document plus its embedding. This is what RAG searches."""

    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Duplicated from the document on purpose: every search filters on it.
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)  # order within the document
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    document: Mapped[Document] = relationship(back_populates="chunks")


class EnquiryStatus:
    DRAFTING = "drafting"  # the workflow is writing a draft
    PENDING_REVIEW = "pending_review"  # paused, waiting for the owner
    APPROVED = "approved"  # owner sent the draft as-is
    EDITED = "edited"  # owner changed the draft, then sent it
    AUTO_SENT = "auto_sent"  # grounded and the business opted in to auto-send
    REJECTED = "rejected"  # owner decided not to reply with it
    FAILED = "failed"  # drafting failed (e.g. AI provider down)
    SEND_FAILED = "send_failed"  # decided, but the email could not be sent
    IGNORED = "ignored"  # automated email (newsletter, auto-reply): no draft, nothing sent


class Enquiry(Base):
    """A customer message and what happened to it. The UI reads this table;
    the LangGraph checkpointer stores the paused workflow (thread_id = id)."""

    __tablename__ = "enquiries"
    # The same email must never become two enquiries (polling can overlap).
    __table_args__ = (
        UniqueConstraint("business_id", "external_id", name="uq_enquiries_business_external"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(16), server_default="web")  # "web" | "email"
    customer_name: Mapped[str | None] = mapped_column(String(200))
    customer_email: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(500))
    message: Mapped[str] = mapped_column(Text)
    # Email threading: Gmail ids plus RFC 5322 headers so replies land in the same thread.
    external_id: Mapped[str | None] = mapped_column(String(200))
    external_thread_id: Mapped[str | None] = mapped_column(String(200))
    message_id_header: Mapped[str | None] = mapped_column(String(500))
    references_header: Mapped[str | None] = mapped_column(Text)
    sent_external_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default=EnquiryStatus.DRAFTING, index=True)
    draft_reply: Mapped[str | None] = mapped_column(Text)
    grounded: Mapped[bool | None] = mapped_column(Boolean)
    citations: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    warnings: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    final_reply: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Append-only, hash-chained log of what happened (per business).

    Each row's hash covers its content plus the previous row's hash, so editing or
    deleting history breaks the chain (see glaka.audit.verify_chain). No message
    text or contact details are stored here: only events, statuses and who acted.
    """

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    enquiry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("enquiries.id", ondelete="SET NULL"), index=True
    )
    event: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(200))  # "system", "customer", "user:<id>"
    details: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
