"""The enquiry workflow as a LangGraph state machine.

    START -> draft --(grounded and auto_send)--> send -> END
                   \\--(otherwise)--> review --(approve/edit)--> send -> END
                                            \\--(reject)--> reject -> END

`review` calls interrupt(): the graph saves its state to the checkpointer and
stops. Hours later, Command(resume=decision) wakes it up at the same spot.
"""

import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any, NotRequired, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from sqlalchemy.orm import Session

from glaka.answer import answer_question
from glaka.audit import record
from glaka.delivery import deliver
from glaka.embeddings import Embedder
from glaka.llm import ChatModel
from glaka.mail import MailClient, MailError
from glaka.models import Business, Enquiry, EnquiryStatus

logger = logging.getLogger(__name__)

SessionScope = Callable[[], AbstractContextManager[Session]]


class EnquiryState(TypedDict):
    """What the graph carries between nodes (and saves when paused)."""

    enquiry_id: str
    draft: NotRequired[dict[str, Any]]  # the Answer, as JSON
    auto_send: NotRequired[bool]
    decision: NotRequired[dict[str, Any]]  # {"action": "approve"|"edit"|"reject", "reply": ...}


def build_workflow(
    session_scope: SessionScope,
    make_embedder: Callable[[], Embedder],
    make_llm: Callable[[], ChatModel],
    make_mail: Callable[[], MailClient],
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    def _load(session: Session, state: EnquiryState) -> Enquiry:
        enquiry = session.get(Enquiry, uuid.UUID(state["enquiry_id"]))
        assert enquiry is not None, "enquiry row must exist before the workflow starts"
        return enquiry

    def draft(state: EnquiryState) -> dict[str, Any]:
        with session_scope() as session:
            enquiry = _load(session, state)
            business = session.get(Business, enquiry.business_id)
            assert business is not None
            question = f"{enquiry.subject}\n\n{enquiry.message}" if enquiry.subject else enquiry.message
            answer = answer_question(
                session, make_embedder(), make_llm(), business.id, business.name, question
            )
            auto_send = business.auto_send and answer.grounded
            record(
                session, business.id, "draft.created", enquiry_id=enquiry.id,
                grounded=answer.grounded, warnings=answer.warnings,
                sources=sorted({c.source for c in answer.citations}), auto_send=auto_send,
            )
            enquiry.draft_reply = answer.answer
            enquiry.grounded = answer.grounded
            enquiry.citations = [c.model_dump(mode="json") for c in answer.citations]
            enquiry.warnings = answer.warnings
            if not auto_send:
                enquiry.status = EnquiryStatus.PENDING_REVIEW
        return {"draft": answer.model_dump(mode="json"), "auto_send": auto_send}

    def route_after_draft(state: EnquiryState) -> str:
        return "send" if state["auto_send"] else "review"

    def review(state: EnquiryState) -> dict[str, Any]:
        # On resume, LangGraph re-runs this node from the top and interrupt()
        # returns the owner's decision. So: no side effects before this line.
        decision = interrupt({"enquiry_id": state["enquiry_id"], "draft": state["draft"]})
        return {"decision": decision}

    def route_after_review(state: EnquiryState) -> str:
        return "reject" if state["decision"]["action"] == "reject" else "send"

    def send(state: EnquiryState) -> dict[str, Any]:
        decision = state.get("decision")
        with session_scope() as session:
            enquiry = _load(session, state)
            actor = (decision or {}).get("actor", "system")
            if decision is None:
                enquiry.status = EnquiryStatus.AUTO_SENT
                enquiry.final_reply = enquiry.draft_reply
                event = "review.auto_approved"
            elif decision["action"] == "edit":
                enquiry.status = EnquiryStatus.EDITED
                enquiry.final_reply = decision["reply"]
                event = "review.edited"
            else:
                enquiry.status = EnquiryStatus.APPROVED
                enquiry.final_reply = enquiry.draft_reply
                event = "review.approved"
            enquiry.reviewed_at = datetime.now(UTC)
            record(session, enquiry.business_id, event, enquiry_id=enquiry.id, actor=actor)
            try:
                deliver(enquiry, make_mail())
                if enquiry.sent_external_id:
                    record(session, enquiry.business_id, "reply.sent", enquiry_id=enquiry.id, channel=enquiry.channel)
            except MailError as exc:
                logger.error("Sending reply for enquiry %s failed: %s", enquiry.id, exc)
                enquiry.status = EnquiryStatus.SEND_FAILED
                record(session, enquiry.business_id, "reply.send_failed", enquiry_id=enquiry.id, error=str(exc)[:300])
        return {}

    def reject(state: EnquiryState) -> dict[str, Any]:
        with session_scope() as session:
            enquiry = _load(session, state)
            enquiry.status = EnquiryStatus.REJECTED
            enquiry.reviewed_at = datetime.now(UTC)
            record(
                session, enquiry.business_id, "review.rejected", enquiry_id=enquiry.id,
                actor=state["decision"].get("actor", "owner"),
            )
        return {}

    graph = StateGraph(EnquiryState)
    graph.add_node("draft", draft)
    graph.add_node("review", review)
    graph.add_node("send", send)
    graph.add_node("reject", reject)
    graph.add_edge(START, "draft")
    graph.add_conditional_edges("draft", route_after_draft, ["send", "review"])
    graph.add_conditional_edges("review", route_after_review, ["send", "reject"])
    graph.add_edge("send", END)
    graph.add_edge("reject", END)
    return graph.compile(checkpointer=checkpointer)
