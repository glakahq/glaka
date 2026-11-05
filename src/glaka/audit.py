"""Write and verify the hash-chained audit trail."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from glaka.guardrails import redact
from glaka.models import AuditEvent

GENESIS = "0" * 64


def _digest(prev_hash: str, business_id: uuid.UUID, enquiry_id: uuid.UUID | None,
            event: str, actor: str, details: dict[str, Any], created_at: datetime) -> str:
    payload = json.dumps(
        {
            "prev": prev_hash,
            "business": str(business_id),
            "enquiry": str(enquiry_id) if enquiry_id else None,
            "event": event,
            "actor": actor,
            "details": details,
            "at": created_at.astimezone(UTC).isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _clean(value: Any) -> Any:
    """Redact personal data from anything going into the log."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


def record(
    session: Session,
    business_id: uuid.UUID,
    event: str,
    *,
    enquiry_id: uuid.UUID | None = None,
    actor: str = "system",
    **details: Any,
) -> AuditEvent:
    """Append an event in the caller's transaction (so it commits with the change it describes)."""
    # One writer per business at a time, so the chain never forks.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"audit:{business_id}"})
    prev = session.scalar(
        select(AuditEvent.hash).where(AuditEvent.business_id == business_id)
        .order_by(AuditEvent.id.desc()).limit(1)
    ) or GENESIS
    details = _clean(details)
    created_at = datetime.now(UTC)
    entry = AuditEvent(
        business_id=business_id, enquiry_id=enquiry_id, event=event, actor=actor,
        details=details, created_at=created_at, prev_hash=prev,
        hash=_digest(prev, business_id, enquiry_id, event, actor, details, created_at),
    )
    session.add(entry)
    session.flush()
    return entry


@dataclass
class ChainCheck:
    ok: bool
    events: int
    first_bad_id: int | None = None
    reason: str | None = None


def verify_chain(session: Session, business_id: uuid.UUID) -> ChainCheck:
    """Recompute every hash in order. Any edit, deletion or reordering shows up here."""
    prev = GENESIS
    count = 0
    rows = session.scalars(
        select(AuditEvent).where(AuditEvent.business_id == business_id).order_by(AuditEvent.id)
    )
    for row in rows:
        count += 1
        if row.prev_hash != prev:
            return ChainCheck(False, count, row.id, "link to previous event is broken (deleted or reordered)")
        expected = _digest(prev, row.business_id, row.enquiry_id, row.event, row.actor, row.details, row.created_at)
        if row.hash != expected:
            return ChainCheck(False, count, row.id, "event content was changed")
        prev = row.hash
    return ChainCheck(True, count)


def events_for(session: Session, enquiry_id: uuid.UUID) -> list[AuditEvent]:
    return list(session.scalars(
        select(AuditEvent).where(AuditEvent.enquiry_id == enquiry_id).order_by(AuditEvent.id)
    ))


def count_events(session: Session, business_id: uuid.UUID) -> int:
    return session.scalar(select(func.count()).where(AuditEvent.business_id == business_id)) or 0
