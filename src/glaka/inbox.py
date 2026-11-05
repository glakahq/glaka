"""The inbox service: submit enquiries and record the owner's decisions.

Both the JSON API and the web UI call this, so the rules live in one place.
"""

import uuid
from typing import Literal

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel, model_validator

from glaka.models import Enquiry, EnquiryStatus
from glaka.workflow import SessionScope


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


class Inbox:
    def __init__(self, session_scope: SessionScope, workflow: CompiledStateGraph) -> None:
        self.session_scope = session_scope
        self.workflow = workflow

    @staticmethod
    def _config(enquiry_id: uuid.UUID) -> dict:
        # One LangGraph "thread" per enquiry: that's how a paused run is found again.
        return {"configurable": {"thread_id": str(enquiry_id)}}

    def submit(
        self, business_id: uuid.UUID, message: str, customer_name: str | None = None
    ) -> uuid.UUID:
        """Store the enquiry, then run the workflow until it pauses or finishes."""
        with self.session_scope() as session:
            enquiry = Enquiry(business_id=business_id, message=message, customer_name=customer_name)
            session.add(enquiry)
            session.flush()
            enquiry_id = enquiry.id
        try:
            self.workflow.invoke({"enquiry_id": str(enquiry_id)}, self._config(enquiry_id))
        except Exception:
            with self.session_scope() as session:
                failed = session.get(Enquiry, enquiry_id)
                if failed is not None:
                    failed.status = EnquiryStatus.FAILED
            raise
        return enquiry_id

    def review(
        self, business_id: uuid.UUID, enquiry_id: uuid.UUID, decision: ReviewDecision
    ) -> None:
        """Resume the paused workflow with the owner's decision."""
        with self.session_scope() as session:
            enquiry = session.get(Enquiry, enquiry_id)
            if enquiry is None or enquiry.business_id != business_id:
                raise EnquiryNotFound(enquiry_id)
            if enquiry.status != EnquiryStatus.PENDING_REVIEW:
                raise NotAwaitingReview(enquiry.status)
        self.workflow.invoke(
            Command(resume=decision.model_dump()), self._config(enquiry_id)
        )
