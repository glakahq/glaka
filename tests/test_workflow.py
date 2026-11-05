"""The approval loop: the workflow pauses for the owner, then resumes."""

import pytest

from glaka.answer import LlmAnswer
from glaka.inbox import NotAwaitingReview, ReviewDecision
from glaka.models import Enquiry


def _get(session, enquiry_id) -> Enquiry:
    session.expire_all()
    return session.get(Enquiry, enquiry_id)


def test_new_enquiry_pauses_for_review(session, shop, inbox) -> None:
    enquiry_id = inbox.submit(shop.id, "shower install costs", "Sam")

    enquiry = _get(session, enquiry_id)
    assert enquiry.status == "pending_review"
    assert enquiry.draft_reply == "It costs £450."
    assert enquiry.grounded is True
    assert enquiry.citations[0]["source"] == "prices.md"
    assert enquiry.final_reply is None
    # LangGraph knows where it stopped: at the review node.
    state = inbox.workflow.get_state({"configurable": {"thread_id": str(enquiry_id)}})
    assert state.next == ("review",)


def test_approve_sends_the_draft(session, shop, inbox) -> None:
    enquiry_id = inbox.submit(shop.id, "shower install costs")

    inbox.review(shop.id, enquiry_id, ReviewDecision(action="approve"))

    enquiry = _get(session, enquiry_id)
    assert enquiry.status == "approved"
    assert enquiry.final_reply == "It costs £450."
    assert enquiry.reviewed_at is not None


def test_edit_sends_the_owners_text(session, shop, inbox) -> None:
    enquiry_id = inbox.submit(shop.id, "shower install costs")

    inbox.review(shop.id, enquiry_id, ReviewDecision(action="edit", reply="£450, fitted this week."))

    enquiry = _get(session, enquiry_id)
    assert enquiry.status == "edited"
    assert enquiry.final_reply == "£450, fitted this week."
    assert enquiry.draft_reply == "It costs £450."  # the AI draft is kept for the record


def test_reject_sends_nothing(session, shop, inbox) -> None:
    enquiry_id = inbox.submit(shop.id, "shower install costs")

    inbox.review(shop.id, enquiry_id, ReviewDecision(action="reject"))

    enquiry = _get(session, enquiry_id)
    assert enquiry.status == "rejected"
    assert enquiry.final_reply is None


def test_cannot_review_twice(session, shop, inbox) -> None:
    enquiry_id = inbox.submit(shop.id, "shower install costs")
    inbox.review(shop.id, enquiry_id, ReviewDecision(action="approve"))

    with pytest.raises(NotAwaitingReview):
        inbox.review(shop.id, enquiry_id, ReviewDecision(action="reject"))


def test_auto_send_skips_review_when_grounded(session, shop, inbox) -> None:
    shop.auto_send = True
    enquiry_id = inbox.submit(shop.id, "shower install costs")

    enquiry = _get(session, enquiry_id)
    assert enquiry.status == "auto_sent"
    assert enquiry.final_reply == "It costs £450."


def test_auto_send_still_waits_when_not_grounded(session, shop, inbox, llm) -> None:
    shop.auto_send = True
    llm.reply = LlmAnswer(can_answer=True, answer="It costs £399.", cited_sources=[1])

    enquiry_id = inbox.submit(shop.id, "shower install costs")

    assert _get(session, enquiry_id).status == "pending_review"


def test_edit_needs_text() -> None:
    with pytest.raises(ValueError):
        ReviewDecision(action="edit", reply="   ")
