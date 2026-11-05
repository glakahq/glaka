"""Owner dashboard: server-rendered HTML (Jinja2, like Thymeleaf).

Every page needs a signed-in user, and every business page checks membership
(get_business). Every form that changes something carries a CSRF token.
"""

import logging
import re
import uuid
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from glaka.audit import record, verify_chain
from glaka.auth import (
    accessible_businesses,
    add_member,
    authenticate,
    csrf_token,
    log_in,
    log_out,
    login_limiter,
    verify_csrf,
)
from glaka.config import get_settings
from glaka.deps import current_user, get_business, get_embedder, get_inbox, get_session
from glaka.embeddings import Embedder
from glaka.inbox import EnquiryNotFound, Inbox, NotAwaitingReview, ReviewDecision
from glaka.llm import LlmError
from glaka.mail import MailError
from glaka.models import AuditEvent, Business, Chunk, Document, Enquiry, EnquiryStatus, Membership, User
from glaka.rag import ingest_document

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")  # autoescapes HTML
templates.env.globals["csrf_token"] = csrf_token

CurrentUser = Annotated[User, Depends(current_user)]
DbSession = Annotated[Session, Depends(get_session)]
CurrentBusiness = Annotated[Business, Depends(get_business)]
Csrf = Depends(verify_csrf)

MAX_UPLOAD_BYTES = 1_000_000
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
LABEL = re.compile(r"^[\w ./-]{1,100}$")

ERRORS = {
    "ai": "The AI provider didn't respond, so no draft was written. Try again in a minute.",
    "stale": "That enquiry has already been dealt with.",
    "empty": "A reply can't be empty. Edit it, or reject the enquiry instead.",
    "mail": "Couldn't reach Gmail. Check the server log, and that Gmail is connected for this business.",
    "send": "The reply was saved but the email couldn't be sent. Use 'Retry sending' on the enquiry.",
    "upload": "Only .md and .txt files up to 1 MB can be added.",
    "nothing": "Add a file, or paste some text with a title.",
    "embed": "Couldn't index the document. Is Ollama running (nomic-embed-text)?",
    "slug": "Use lower-case letters, digits and dashes for the short id (e.g. smith-plumbing).",
    "taken": "That short id is already in use.",
    "label": "The Gmail label can contain letters, digits, spaces and . / - _ only.",
    "login": "Wrong email or password.",
    "throttled": "Too many attempts. Try again in 15 minutes.",
}
NOTICES = {
    "saved": "Settings saved.",
    "added": "Added to the knowledge base.",
    "removed": "Removed from the knowledge base.",
    "created": "Business created. Add its documents in Knowledge, then connect email.",
}


def _actor(user: User) -> str:
    return f"user:{user.id}"


def _back(path: str, error: str | None = None, notice: str | None = None, **counts: int) -> RedirectResponse:
    params = [f"error={error}"] if error else []
    params += [f"notice={notice}"] if notice else []
    params += [f"{k}={v}" for k, v in counts.items()]
    return RedirectResponse(path + (f"?{'&'.join(params)}" if params else ""), status_code=303)


def _page(request: Request, name: str, user: User, session: Session, **context):
    """Render a page with what every page needs (user, their businesses, messages)."""
    business = context.get("business")
    return templates.TemplateResponse(
        request,
        name,
        {
            "user": user,
            "businesses": accessible_businesses(session, user),
            "error": ERRORS.get(request.query_params.get("error", "")),
            "notice": NOTICES.get(request.query_params.get("notice", "")),
            "gmail_ready": bool(business) and get_settings().gmail_token_file(business.id).exists(),
            **context,
        },
    )


# --- sign in / out ----------------------------------------------------------------------


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/ui")


def _safe_next(target: str) -> str:
    """Only redirect within our own UI (no open redirects to other sites)."""
    return target if target.startswith("/ui") and not target.startswith("//") else "/ui"


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/ui", error: str | None = None):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": _safe_next(next), "error": ERRORS.get(error or ""), "user": None, "businesses": []},
    )


@router.post("/login", dependencies=[Csrf])
def login(
    request: Request,
    session: DbSession,
    email: Annotated[str, Form(max_length=320)],
    password: Annotated[str, Form(max_length=500)],
    next: Annotated[str, Form()] = "/ui",
) -> RedirectResponse:
    target = _safe_next(next)
    ip = request.client.host if request.client else "?"
    if login_limiter.blocked(ip, email):
        return RedirectResponse(f"/login?next={quote(target)}&error=throttled", status_code=303)
    user = authenticate(session, email, password)
    if user is None:
        login_limiter.failed(ip, email)
        return RedirectResponse(f"/login?next={quote(target)}&error=login", status_code=303)
    login_limiter.succeeded(ip, email)
    log_in(request, user)
    return RedirectResponse(target, status_code=303)


@router.post("/logout", dependencies=[Csrf])
def logout(request: Request) -> RedirectResponse:
    log_out(request)
    return RedirectResponse("/login", status_code=303)


# --- businesses -------------------------------------------------------------------------


@router.get("/ui", response_class=HTMLResponse)
def businesses_page(request: Request, user: CurrentUser, session: DbSession):
    pending = dict(
        session.execute(
            select(Enquiry.business_id, func.count())
            .where(Enquiry.status == EnquiryStatus.PENDING_REVIEW)
            .group_by(Enquiry.business_id)
        ).all()
    )
    return _page(request, "businesses.html", user, session, pending=pending)


@router.post("/ui/businesses", dependencies=[Csrf])
def create_business(
    user: CurrentUser,
    session: DbSession,
    slug: Annotated[str, Form(max_length=64)],
    name: Annotated[str, Form(min_length=1, max_length=200)],
) -> RedirectResponse:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can create businesses")
    slug = slug.strip().lower()
    if not SLUG.match(slug):
        return _back("/ui", error="slug")
    if session.scalar(select(Business.id).where(Business.slug == slug)):
        return _back("/ui", error="taken")
    business = Business(slug=slug, name=name.strip())
    session.add(business)
    session.flush()
    add_member(session, user, business, role="owner")
    record(session, business.id, "business.created", actor=_actor(user))
    return _back(f"/ui/{slug}/settings", notice="created")


# --- inbox ------------------------------------------------------------------------------


def _names(session: Session) -> dict[str, str]:
    """Map audit actors ("user:<id>") to display names."""
    return {f"user:{u.id}": u.name for u in session.scalars(select(User))}


@router.get("/ui/{slug}", response_class=HTMLResponse)
def inbox_page(
    request: Request,
    user: CurrentUser,
    business: CurrentBusiness,
    session: DbSession,
    new: int | None = None,
    seen: int | None = None,
    failed: int | None = None,
    ignored: int | None = None,
):
    enquiries = session.scalars(
        select(Enquiry).where(Enquiry.business_id == business.id).order_by(Enquiry.created_at.desc()).limit(200)
    ).all()
    activity: dict = {}
    for event in session.scalars(
        select(AuditEvent)
        .where(AuditEvent.business_id == business.id, AuditEvent.enquiry_id.in_([e.id for e in enquiries]))
        .order_by(AuditEvent.id)
    ):
        activity.setdefault(event.enquiry_id, []).append(event)
    poll_notice = None
    if new is not None:
        poll_notice = f"Checked email: {new} new, {seen or 0} already in the inbox"
        poll_notice += f", {ignored} automated (not drafted)" if ignored else ""
        poll_notice += f", {failed} couldn't be drafted (AI unavailable)." if failed else "."
    return _page(
        request, "inbox.html", user, session,
        business=business, tab="inbox",
        pending=[e for e in enquiries if e.status == EnquiryStatus.PENDING_REVIEW],
        history=[e for e in enquiries if e.status != EnquiryStatus.PENDING_REVIEW],
        activity=activity, names=_names(session), poll_notice=poll_notice,
    )


@router.post("/ui/{slug}/enquiries", dependencies=[Csrf])
def submit_enquiry(
    slug: str,
    business: CurrentBusiness,
    inbox: Annotated[Inbox, Depends(get_inbox)],
    message: Annotated[str, Form(min_length=1, max_length=5000)],
    customer_name: Annotated[str, Form(max_length=200)] = "",
) -> RedirectResponse:
    try:
        inbox.submit(business.id, message.strip(), customer_name.strip() or None)
    except LlmError as exc:
        logger.error("LLM provider error: %s", exc)
        return _back(f"/ui/{slug}", error="ai")
    return _back(f"/ui/{slug}")


@router.post("/ui/{slug}/enquiries/{enquiry_id}/review", dependencies=[Csrf])
def review_enquiry(
    slug: str,
    enquiry_id: uuid.UUID,
    user: CurrentUser,
    business: CurrentBusiness,
    inbox: Annotated[Inbox, Depends(get_inbox)],
    session: DbSession,
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
        return _back(f"/ui/{slug}", error="empty")
    try:
        inbox.review(business.id, enquiry_id, decision, actor=_actor(user))
    except EnquiryNotFound:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    except NotAwaitingReview:
        return _back(f"/ui/{slug}", error="stale")
    session.expire_all()
    if session.get(Enquiry, enquiry_id).status == EnquiryStatus.SEND_FAILED:
        return _back(f"/ui/{slug}", error="send")
    return _back(f"/ui/{slug}")


@router.post("/ui/{slug}/email/poll", dependencies=[Csrf])
def poll_email(
    slug: str, business: CurrentBusiness, inbox: Annotated[Inbox, Depends(get_inbox)]
) -> RedirectResponse:
    try:
        result = inbox.poll_email(business.id)
    except MailError as exc:
        logger.error("Email poll failed: %s", exc)
        return _back(f"/ui/{slug}", error="mail")
    return _back(f"/ui/{slug}", new=result.new, seen=result.already_seen, failed=result.failed, ignored=result.ignored)


@router.post("/ui/{slug}/enquiries/{enquiry_id}/retry-send", dependencies=[Csrf])
def retry_send(
    slug: str,
    enquiry_id: uuid.UUID,
    user: CurrentUser,
    business: CurrentBusiness,
    inbox: Annotated[Inbox, Depends(get_inbox)],
) -> RedirectResponse:
    try:
        inbox.retry_send(business.id, enquiry_id, actor=_actor(user))
    except EnquiryNotFound:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    except NotAwaitingReview:
        return _back(f"/ui/{slug}", error="stale")
    except MailError as exc:
        logger.error("Retry send failed: %s", exc)
        return _back(f"/ui/{slug}", error="send")
    return _back(f"/ui/{slug}")


# --- knowledge base ---------------------------------------------------------------------


@router.get("/ui/{slug}/knowledge", response_class=HTMLResponse)
def knowledge_page(request: Request, user: CurrentUser, business: CurrentBusiness, session: DbSession):
    documents = session.execute(
        select(Document, func.count(Chunk.id))
        .outerjoin(Chunk)
        .where(Document.business_id == business.id)
        .group_by(Document.id)
        .order_by(Document.source)
    ).all()
    return _page(request, "knowledge.html", user, session, business=business, tab="knowledge", documents=documents)


def _source_name(title: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80] or "note"
    return f"{stem}.md"


@router.post("/ui/{slug}/knowledge", dependencies=[Csrf])
async def add_knowledge(
    slug: str,
    user: CurrentUser,
    business: CurrentBusiness,
    session: DbSession,
    embedder: Annotated[Embedder, Depends(get_embedder)],
    files: Annotated[list[UploadFile] | None, File()] = None,
    title: Annotated[str, Form(max_length=200)] = "",
    text: Annotated[str, Form(max_length=200_000)] = "",
) -> RedirectResponse:
    items: list[tuple[str, str, str]] = []  # (source, title, text)
    for upload in files or []:
        if not upload.filename:
            continue
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES or Path(upload.filename).suffix.lower() not in {".md", ".txt"}:
            return _back(f"/ui/{slug}/knowledge", error="upload")
        content = data.decode("utf-8", errors="replace")
        first = content.lstrip().splitlines()[0] if content.strip() else ""
        doc_title = first.lstrip("# ").strip() if first.startswith("#") else Path(upload.filename).stem
        items.append((Path(upload.filename).name, doc_title, content))
    if text.strip():
        if not title.strip():
            return _back(f"/ui/{slug}/knowledge", error="nothing")
        items.append((_source_name(title), title.strip(), f"# {title.strip()}\n\n{text.strip()}"))
    if not items:
        return _back(f"/ui/{slug}/knowledge", error="nothing")
    try:
        for source, doc_title, content in items:
            doc = ingest_document(session, embedder, business.id, source, doc_title, content)
            record(session, business.id, "knowledge.added", actor=_actor(user), source=source, chunks=len(doc.chunks))
    except Exception as exc:  # embedding service down, etc.
        logger.error("Ingest failed: %s", exc)
        session.rollback()
        return _back(f"/ui/{slug}/knowledge", error="embed")
    return _back(f"/ui/{slug}/knowledge", notice="added")


@router.post("/ui/{slug}/knowledge/{document_id}/delete", dependencies=[Csrf])
def delete_knowledge(
    slug: str, document_id: uuid.UUID, user: CurrentUser, business: CurrentBusiness, session: DbSession
) -> RedirectResponse:
    document = session.get(Document, document_id)
    if document is None or document.business_id != business.id:
        raise HTTPException(status_code=404, detail="Document not found")
    record(session, business.id, "knowledge.removed", actor=_actor(user), source=document.source)
    session.delete(document)
    return _back(f"/ui/{slug}/knowledge", notice="removed")


# --- settings ---------------------------------------------------------------------------


@router.get("/ui/{slug}/settings", response_class=HTMLResponse)
def settings_page(request: Request, user: CurrentUser, business: CurrentBusiness, session: DbSession):
    members = session.execute(
        select(User, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.business_id == business.id)
        .order_by(User.name)
    ).all()
    return _page(request, "settings.html", user, session, business=business, tab="settings", members=members)


@router.post("/ui/{slug}/settings", dependencies=[Csrf])
def save_settings(
    slug: str,
    user: CurrentUser,
    business: CurrentBusiness,
    session: DbSession,
    name: Annotated[str, Form(min_length=1, max_length=200)],
    email_label: Annotated[str, Form(max_length=100)],
    reply_instructions: Annotated[str, Form(max_length=2000)] = "",
    auto_send: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    if not LABEL.match(email_label.strip()):
        return _back(f"/ui/{slug}/settings", error="label")
    new = {
        "name": name.strip(),
        "email_label": email_label.strip(),
        "reply_instructions": reply_instructions.strip() or None,
        "auto_send": auto_send == "on",
    }
    changed = sorted(k for k, v in new.items() if getattr(business, k) != v)
    for key, value in new.items():
        setattr(business, key, value)
    if changed:
        record(session, business.id, "settings.updated", actor=_actor(user), fields=changed)
    return _back(f"/ui/{slug}/settings", notice="saved")


# --- activity (audit trail) -------------------------------------------------------------


@router.get("/ui/{slug}/activity", response_class=HTMLResponse)
def activity_page(request: Request, user: CurrentUser, business: CurrentBusiness, session: DbSession):
    events = session.scalars(
        select(AuditEvent).where(AuditEvent.business_id == business.id).order_by(AuditEvent.id.desc()).limit(300)
    ).all()
    return _page(
        request, "activity.html", user, session,
        business=business, tab="activity", events=events,
        chain=verify_chain(session, business.id), names=_names(session),
    )
