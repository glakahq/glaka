"""Public endpoints for the website chat widget (no sign-in; protected differently).

- Each business has a public widget key (rotatable) and a list of websites allowed to
  embed it (CORS). Disabled widgets and unknown keys are 404.
- Rate limits per visitor IP and per business, so nobody can run up the AI bill.
- Each conversation has a private random token that only the visitor's browser holds;
  we store its SHA-256. Without the token, nobody can read the replies.
- Messages become normal enquiries (channel "chat"): same RAG, guardrails, audit and
  approval. Replies appear in the chat once approved (or instantly with auto-send).
"""

import hashlib
import logging
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from glaka.deps import get_inbox, get_session
from glaka.inbox import Inbox
from glaka.llm import LlmError
from glaka.models import Business, Enquiry, EnquiryStatus
from glaka.ratelimit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(tags=["widget"])

STATIC = Path(__file__).parent / "static"
REPLIED = {EnquiryStatus.APPROVED, EnquiryStatus.EDITED, EnquiryStatus.AUTO_SENT}
DEFAULT_GREETING = "Hi! Ask us anything about our services and prices."
PENDING_NOTE = "Thanks! A member of the team will check the answer and reply here shortly."

per_visitor = RateLimiter(limit=8, window_seconds=10 * 60)
per_business = RateLimiter(limit=300, window_seconds=24 * 3600)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def new_widget_key() -> str:
    return "wk_" + secrets.token_urlsafe(18)


def conversation_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def allowed_origins(business: Business) -> list[str]:
    return [o.strip().rstrip("/") for o in (business.widget_allowed_origins or "").splitlines() if o.strip()]


def _origin_ok(business: Business, request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:  # not a cross-site browser request (curl, same-origin GET)
        return True
    own = f"{request.url.scheme}://{request.url.netloc}"
    allowed = allowed_origins(business)
    return origin == own or not allowed or origin.rstrip("/") in allowed


def _cors(response: Response, request: Request) -> None:
    origin = request.headers.get("origin")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "600"


def widget_business(key: str, request: Request, response: Response,
                    session: Annotated[Session, Depends(get_session)]) -> Business:
    business = session.scalar(select(Business).where(Business.widget_key == key))
    if business is None or not business.widget_enabled:
        raise HTTPException(status_code=404, detail="Unknown or disabled widget")
    if not _origin_ok(business, request):
        raise HTTPException(status_code=403, detail="This website may not use this widget")
    _cors(response, request)
    return business


WidgetBusiness = Annotated[Business, Depends(widget_business)]


# --- the script -------------------------------------------------------------------------


@router.get("/widget.js", include_in_schema=False)
def widget_script() -> FileResponse:
    return FileResponse(
        STATIC / "widget.js",
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.options("/widget/{key}/{rest:path}", include_in_schema=False)
def preflight(key: str, rest: str, request: Request, business: WidgetBusiness) -> Response:
    response = Response(status_code=204)
    _cors(response, request)
    return response


# --- API used by the script ---------------------------------------------------------------


class WidgetConfig(BaseModel):
    business_name: str
    greeting: str


@router.get("/widget/{key}/config")
def config(business: WidgetBusiness) -> WidgetConfig:
    return WidgetConfig(business_name=business.name, greeting=business.widget_greeting or DEFAULT_GREETING)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=320)
    conversation: str | None = Field(default=None, min_length=20, max_length=100)

    @field_validator("email")
    @classmethod
    def plausible_email(cls, value: str | None) -> str | None:
        if value and not _EMAIL.match(value.strip()):
            raise ValueError("not an email address")
        return value.strip().lower() if value else None


class ChatOut(BaseModel):
    conversation: str
    status: Literal["answered", "pending"]
    reply: str | None = None
    note: str | None = None


@router.post("/widget/{key}/messages")
def send_message(
    key: str,
    body: ChatIn,
    request: Request,
    business: WidgetBusiness,
    inbox: Annotated[Inbox, Depends(get_inbox)],
    session: Annotated[Session, Depends(get_session)],
) -> ChatOut:
    ip = request.client.host if request.client else "?"
    if not per_visitor.allow(f"{business.id}:{ip}") or not per_business.allow(str(business.id)):
        raise HTTPException(status_code=429, detail="Too many messages. Please try again later.")
    token = body.conversation or secrets.token_urlsafe(32)
    try:
        enquiry_id = inbox.submit(
            business.id, body.message.strip(), (body.name or "").strip() or None,
            channel="chat", customer_email=body.email, conversation_hash=conversation_hash(token),
        )
    except LlmError as exc:  # stored as failed; the owner will see it
        logger.error("Chat drafting failed: %s", exc)
        return ChatOut(conversation=token, status="pending", note=PENDING_NOTE)
    session.expire_all()
    enquiry = session.get(Enquiry, enquiry_id)
    if enquiry is not None and enquiry.status in REPLIED:
        return ChatOut(conversation=token, status="answered", reply=enquiry.final_reply)
    return ChatOut(conversation=token, status="pending", note=PENDING_NOTE)


class ChatMessage(BaseModel):
    role: Literal["visitor", "business"]
    text: str
    at: datetime


@router.get("/widget/{key}/conversations/{token}")
def conversation(
    token: str, business: WidgetBusiness, session: Annotated[Session, Depends(get_session)]
) -> list[ChatMessage]:
    enquiries = session.scalars(
        select(Enquiry)
        .where(Enquiry.business_id == business.id, Enquiry.conversation_hash == conversation_hash(token))
        .order_by(Enquiry.created_at)
    )
    messages: list[ChatMessage] = []
    for enquiry in enquiries:
        messages.append(ChatMessage(role="visitor", text=enquiry.message, at=enquiry.created_at))
        if enquiry.status in REPLIED and enquiry.final_reply:
            messages.append(ChatMessage(role="business", text=enquiry.final_reply,
                                        at=enquiry.reviewed_at or enquiry.created_at))
    return messages
