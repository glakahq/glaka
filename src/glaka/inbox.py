"""The inbox service: submit enquiries and record the owner's decisions.

Both the JSON API and the web UI call this, so the rules live in one place.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel, model_validator
from sqlalchemy import select

from glaka.audit import record
from glaka.delivery import deliver
from glaka.gmail_mcp.schemas import Email
from glaka.llm import LlmError
from glaka.mail import MailClient
from glaka.models import Enquiry, EnquiryStatus
from glaka.workflow import SessionScope

logger = logging.getLogger(__name__)


class EnquiryNotFound(Exception):
    pass


class NotAwaitingReview(Exception):
    pass


class ReviewDecision(BaseModel):
    action: Literal["approve", "edit", "reject"]
    reply: str | None = None

    @model_validator(mode="after")
    def edit_needs_reply(self) -> "ReviewDecision":
        if self.action == "edit" and not (self.reply and self.reply.strip()):
            raise ValueError("an edited reply cannot be empty")
        return self


@dataclass
class PollResult:
    new: int = 0
    already_seen: int = 0
    ignored: int = 0  # automated emails, stored for visibility but not drafted
    failed: int = 0  # stored, but drafting failed (e.g. AI provider down)


class Inbox:
    def __init__(
        self,
        session_scope: SessionScope,
        workflow: CompiledStateGraph,
        make_mail: Callable[[], MailClient],
    ) -> None:
        self.session_scope = session_scope
        self.workflow = workflow
        self.make_mail = make_mail

    @staticmethod
    def _config(enquiry_id: uuid.UUID) -> dict:
        # One LangGraph "thread" per enquiry: that's how a paused run is found again.
        return {"configurable": {"thread_id": str(enquiry_id)}}

    def submit(
        self,
        business_id: uuid.UUID,
        message: str,
        customer_name: str | None = None,
        email: Email | None = None,
    ) -> uuid.UUID:
        """Store the enquiry, then run the workflow until it pauses or finishes."""
        with self.session_scope() as session:
            enquiry = self._new_enquiry(business_id, message, customer_name, email)
            session.add(enquiry)
            session.flush()
            enquiry_id = enquiry.id
            record(session, business_id, "enquiry.received", enquiry_id=enquiry_id,
                   actor="customer", channel=enquiry.channel)
        try:
            self.workflow.invoke({"enquiry_id": str(enquiry_id)}, self._config(enquiry_id))
        except Exception as exc:
            with self.session_scope() as session:
                failed = session.get(Enquiry, enquiry_id)
                if failed is not None:
                    failed.status = EnquiryStatus.FAILED
                    record(session, business_id, "draft.failed", enquiry_id=enquiry_id,
                           error=type(exc).__name__)
            raise
        return enquiry_id

    @staticmethod
    def _new_enquiry(
        business_id: uuid.UUID, message: str, customer_name: str | None, email: Email | None
    ) -> Enquiry:
        enquiry = Enquiry(business_id=business_id, message=message, customer_name=customer_name)
        if email is not None:
            enquiry.channel = "email"
            enquiry.customer_name = email.from_name or None
            enquiry.customer_email = email.from_address
            enquiry.subject = email.subject
            enquiry.external_id = email.id
            enquiry.external_thread_id = email.thread_id
            enquiry.message_id_header = email.message_id_header
            enquiry.references_header = email.references
        return enquiry

    def review(
        self,
        business_id: uuid.UUID,
        enquiry_id: uuid.UUID,
        decision: ReviewDecision,
        actor: str = "owner",
    ) -> None:
        """Resume the paused workflow with the owner's decision."""
        with self.session_scope() as session:
            enquiry = session.get(Enquiry, enquiry_id)
            if enquiry is None or enquiry.business_id != business_id:
                raise EnquiryNotFound(enquiry_id)
            if enquiry.status != EnquiryStatus.PENDING_REVIEW:
                raise NotAwaitingReview(enquiry.status)
        self.workflow.invoke(
            Command(resume={**decision.model_dump(), "actor": actor}), self._config(enquiry_id)
        )

    def poll_email(self, business_id: uuid.UUID) -> PollResult:
        """Fetch labelled emails and turn each new one into an enquiry."""
        result = PollResult()
        for email in self.make_mail().fetch_new():
            with self.session_scope() as session:
                seen = session.scalar(
                    select(Enquiry.id).where(
                        Enquiry.business_id == business_id, Enquiry.external_id == email.id
                    )
                )
            if seen:
                result.already_seen += 1
                continue
            if email.automated_reason:
                # Input guardrail: don't draft replies to newsletters or auto-replies.
                with self.session_scope() as session:
                    ignored = self._new_enquiry(business_id, email.body, None, email)
                    ignored.status = EnquiryStatus.IGNORED
                    ignored.warnings = [f"not drafted: {email.automated_reason}"]
                    session.add(ignored)
                    session.flush()
                    record(session, business_id, "enquiry.ignored", enquiry_id=ignored.id,
                           channel="email", reason=email.automated_reason)
                result.ignored += 1
                continue
            try:
                self.submit(business_id, email.body or "(empty message)", email=email)
                result.new += 1
            except LlmError as exc:
                logger.error("Drafting failed for email %s: %s", email.id, exc)
                result.failed += 1
        return result

    def retry_send(self, business_id: uuid.UUID, enquiry_id: uuid.UUID, actor: str = "owner") -> None:
        """Try again to send a reply that was decided but not delivered. Raises MailError."""
        with self.session_scope() as session:
            enquiry = session.get(Enquiry, enquiry_id)
            if enquiry is None or enquiry.business_id != business_id:
                raise EnquiryNotFound(enquiry_id)
            if enquiry.status != EnquiryStatus.SEND_FAILED:
                raise NotAwaitingReview(enquiry.status)
            deliver(enquiry, self.make_mail())
            record(session, business_id, "reply.sent", enquiry_id=enquiry_id, actor=actor,
                   channel=enquiry.channel, retry=True)
            edited = (enquiry.final_reply or "").strip() != (enquiry.draft_reply or "").strip()
            enquiry.status = EnquiryStatus.EDITED if edited else EnquiryStatus.APPROVED
