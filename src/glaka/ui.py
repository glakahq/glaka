"""Owner inbox web UI: server-rendered HTML (Jinja2, like Thymeleaf).

Dev-only for now: there is no login yet (that comes with the dashboard, step 6).
Do not expose it on the internet.
"""

import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from glaka.config import get_settings
from glaka.deps import get_business, get_inbox, get_session
from glaka.inbox import EnquiryNotFound, Inbox, NotAwaitingReview, ReviewDecision
from glaka.llm import LlmError
from glaka.mail import MailError
from glaka.models import AuditEvent, Business, Chunk, Document, Enquiry, EnquiryStatus

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")  # autoescapes HTML

ERRORS = {
    "ai": "The AI provider didn't respond, so no draft was written. Try again in a minute.",
    "stale": "That enquiry has already been dealt with.",
    "empty": "A reply can't be empty. Edit it, or reject the enquiry instead.",
    "mail": "Couldn't reach Gmail. Check the server log, and that Gmail is authorised (docs/gmail-setup.md).",
    "send": "The reply was saved but the email couldn't be sent. Use 'Retry sending' on the enquiry.",
}


def _back(slug: str, error: str | None = None, **notice: int) -> RedirectResponse:
    params = [f"error={error}"] if error else []
    params += [f"{key}={value}" for key, value in notice.items()]
    url = f"/ui/{slug}" + (f"?{'&'.join(params)}" if params else "")
    return RedirectResponse(url, status_code=303)  # 303: POST -> redirect -> GET


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/ui")


@router.get("/ui", response_class=HTMLResponse)
def businesses_page(request: Request, session: Annotated[Session, Depends(get_session)]):
    pending = func.count(Enquiry.id).filter(Enquiry.status == EnquiryStatus.PENDING_REVIEW)
    rows = session.execute(
        select(Business, pending)
        .outerjoin(Enquiry, Enquiry.business_id == Business.id)
        .group_by(Business.id)
        .order_by(Business.name)
    ).all()
    return templates.TemplateResponse(request, "businesses.html", {"rows": rows})


@router.get("/ui/{slug}", response_class=HTMLResponse)
def inbox_page(
    request: Request,
    business: Annotated[Business, Depends(get_business)],
    session: Annotated[Session, Depends(get_session)],
    error: str | None = None,
    new: int | None = None,
    seen: int | None = None,
    failed: int | None = None,
    ignored: int | None = None,
):
    enquiries = session.scalars(
        select(Enquiry).where(Enquiry.business_id == business.id).order_by(Enquiry.created_at.desc())
    ).all()
    documents = session.execute(
        select(Document, func.count(Chunk.id))
        .outerjoin(Chunk)
        .where(Document.business_id == business.id)
        .group_by(Document.id)
        .order_by(Document.source)
    ).all()
    all_businesses = session.scalars(select(Business).order_by(Business.name)).all()
    activity: dict = {}
    for event in session.scalars(
        select(AuditEvent).where(AuditEvent.business_id == business.id, AuditEvent.enquiry_id.is_not(None))
        .order_by(AuditEvent.id)
    ):
        activity.setdefault(event.enquiry_id, []).append(event)
    settings = get_settings()
    notice = None
    if new is not None:
        notice = f"Checked email: {new} new, {seen or 0} already in the inbox"
        if ignored:
            notice += f", {ignored} automated (not drafted)"
        notice += f", {failed} couldn't be drafted (AI unavailable)." if failed else "."
    return templates.TemplateResponse(
        request,
        "inbox.html",
        {
            "business": business,
            "businesses": all_businesses,
            "pending": [e for e in enquiries if e.status == EnquiryStatus.PENDING_REVIEW],
            "history": [e for e in enquiries if e.status != EnquiryStatus.PENDING_REVIEW],
            "documents": documents,
            "activity": activity,
            "error": ERRORS.get(error or ""),
            "notice": notice,
            "gmail_ready": settings.gmail_token_file.exists(),
            "gmail_label": settings.gmail_label,
        },
    )


@router.post("/ui/{slug}/enquiries")
def submit_enquiry(
    slug: str,
    business: Annotated[Business, Depends(get_business)],
    inbox: Annotated[Inbox, Depends(get_inbox)],
    message: Annotated[str, Form(min_length=1, max_length=5000)],
    customer_name: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    try:
        inbox.submit(business.id, message.strip(), customer_name.strip() or None)
    except LlmError as exc:
        logger.error("LLM provider error: %s", exc)
        return _back(slug, "ai")
    return _back(slug)


@router.post("/ui/{slug}/enquiries/{enquiry_id}/review")
def review_enquiry(
    slug: str,
    enquiry_id: uuid.UUID,
    business: Annotated[Business, Depends(get_business)],
    inbox: Annotated[Inbox, Depends(get_inbox)],
    session: Annotated[Session, Depends(get_session)],
    action: Annotated[str, Form()],
    reply: Annotated[str, Form()] = "",
) -> RedirectResponse:
    enquiry = session.get(Enquiry, enquiry_id)
    if enquiry is None or enquiry.business_id != business.id:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    try:
        if action == "reject":
            decision = ReviewDecision(action="reject")
        elif reply.strip() == (enquiry.draft_reply or "").strip():
            decision = ReviewDecision(action="approve")
        else:
            decision = ReviewDecision(action="edit", reply=reply.strip())
    except ValidationError:
        return _back(slug, "empty")
    try:
        inbox.review(business.id, enquiry_id, decision)
    except EnquiryNotFound:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    except NotAwaitingReview:
        return _back(slug, "stale")
    session.expire_all()
    if session.get(Enquiry, enquiry_id).status == EnquiryStatus.SEND_FAILED:
        return _back(slug, "send")
    return _back(slug)


@router.post("/ui/{slug}/email/poll")
def poll_email(
    slug: str,
    business: Annotated[Business, Depends(get_business)],
    inbox: Annotated[Inbox, Depends(get_inbox)],
) -> RedirectResponse:
    try:
        result = inbox.poll_email(business.id)
    except MailError as exc:
        logger.error("Email poll failed: %s", exc)
        return _back(slug, "mail")
    return _back(
        slug, new=result.new, seen=result.already_seen, failed=result.failed, ignored=result.ignored
    )


@router.post("/ui/{slug}/enquiries/{enquiry_id}/retry-send")
def retry_send(
    slug: str,
    enquiry_id: uuid.UUID,
    business: Annotated[Business, Depends(get_business)],
    inbox: Annotated[Inbox, Depends(get_inbox)],
) -> RedirectResponse:
    try:
        inbox.retry_send(business.id, enquiry_id)
    except EnquiryNotFound:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    except NotAwaitingReview:
        return _back(slug, "stale")
    except MailError as exc:
        logger.error("Retry send failed: %s", exc)
        return _back(slug, "send")
    return _back(slug)
