"""JSON API."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from glaka.answer import Answer, answer_question
from glaka.deps import get_business, get_embedder, get_inbox, get_llm, get_session
from glaka.embeddings import Embedder
from glaka.inbox import EnquiryNotFound, Inbox, NotAwaitingReview, ReviewDecision
from glaka.llm import ChatModel
from glaka.models import Business, Enquiry

router = APIRouter()

Slug = Annotated[str, Path(description="The business's short id", examples=["demo-bathrooms"])]


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness check: returns 200 if the process is up."""
    return {"status": "ok"}


class AskRequest(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=2000,
        description="The customer's message",
        examples=["How much is a walk-in shower?"],
    )


@router.post("/businesses/{slug}/ask")
def ask(
    slug: Slug,
    request: AskRequest,
    business: Annotated[Business, Depends(get_business)],
    session: Annotated[Session, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[ChatModel, Depends(get_llm)],
) -> Answer:
    """Draft a grounded reply (no review, nothing stored). Handy for trying questions."""
    return answer_question(session, embedder, llm, business.id, business.name, request.question)


class EnquiryIn(BaseModel):
    message: str = Field(min_length=1, max_length=5000, examples=["How much is a walk-in shower?"])
    customer_name: str | None = Field(default=None, max_length=200, examples=["Sam"])


class EnquiryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # build from the SQLAlchemy object

    id: uuid.UUID
    customer_name: str | None
    message: str
    status: str
    draft_reply: str | None
    grounded: bool | None
    citations: list
    warnings: list
    final_reply: str | None
    created_at: datetime
    reviewed_at: datetime | None


def _enquiry(session: Session, business: Business, enquiry_id: uuid.UUID) -> Enquiry:
    enquiry = session.get(Enquiry, enquiry_id)
    if enquiry is None or enquiry.business_id != business.id:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    return enquiry


@router.post("/businesses/{slug}/enquiries", status_code=201)
def submit_enquiry(
    slug: Slug,
    body: EnquiryIn,
    business: Annotated[Business, Depends(get_business)],
    inbox: Annotated[Inbox, Depends(get_inbox)],
    session: Annotated[Session, Depends(get_session)],
) -> EnquiryOut:
    """A customer message arrives: draft a reply and wait for the owner (or auto-send)."""
    enquiry_id = inbox.submit(business.id, body.message, body.customer_name)
    session.expire_all()  # the workflow wrote in its own transaction; re-read
    return EnquiryOut.model_validate(_enquiry(session, business, enquiry_id))


@router.get("/businesses/{slug}/enquiries")
def list_enquiries(
    slug: Slug,
    business: Annotated[Business, Depends(get_business)],
    session: Annotated[Session, Depends(get_session)],
) -> list[EnquiryOut]:
    rows = session.scalars(
        select(Enquiry).where(Enquiry.business_id == business.id).order_by(Enquiry.created_at.desc())
    )
    return [EnquiryOut.model_validate(e) for e in rows]


@router.post("/businesses/{slug}/enquiries/{enquiry_id}/review")
def review_enquiry(
    slug: Slug,
    enquiry_id: uuid.UUID,
    decision: ReviewDecision,
    business: Annotated[Business, Depends(get_business)],
    inbox: Annotated[Inbox, Depends(get_inbox)],
    session: Annotated[Session, Depends(get_session)],
) -> EnquiryOut:
    """The owner approves, edits or rejects a pending draft."""
    try:
        inbox.review(business.id, enquiry_id, decision)
    except EnquiryNotFound:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    except NotAwaitingReview as exc:
        raise HTTPException(status_code=409, detail=f"Enquiry is {exc}, not pending review")
    session.expire_all()
    return EnquiryOut.model_validate(_enquiry(session, business, enquiry_id))
