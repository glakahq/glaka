"""Data shapes exchanged over MCP (tool inputs and outputs)."""

from pydantic import BaseModel


class Email(BaseModel):
    id: str  # Gmail message id
    thread_id: str
    from_name: str
    from_address: str
    subject: str
    date: str
    body: str
    message_id_header: str  # RFC 5322 Message-ID, used for In-Reply-To
    references: str
    automated_reason: str | None = None  # set if it looks machine-sent (newsletter, auto-reply...)


class SentEmail(BaseModel):
    id: str
    thread_id: str
