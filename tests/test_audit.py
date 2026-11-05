"""The audit trail: right events, no personal data, and tampering is detected."""

from sqlalchemy import update

from glaka.audit import events_for, record, verify_chain
from glaka.gmail_mcp.schemas import Email
from glaka.inbox import ReviewDecision
from glaka.models import AuditEvent


def test_review_flow_is_logged_in_order(session, shop, inbox) -> None:
    enquiry_id = inbox.submit(shop.id, "shower install costs")
    inbox.review(shop.id, enquiry_id, ReviewDecision(action="approve"), actor="user:alice")

    events = [(e.event, e.actor) for e in events_for(session, enquiry_id)]

    assert events == [
        ("enquiry.received", "customer"),
        ("draft.created", "system"),
        ("review.approved", "user:alice"),
    ]
    assert verify_chain(session, shop.id).ok


def test_email_reply_sent_is_logged(session, shop, inbox, mail) -> None:
    mail.emails = [Email(id="m1", thread_id="t1", from_name="Sam", from_address="sam@example.com",
                         subject="Shower", date="", body="shower install costs",
                         message_id_header="<m1@x>", references="")]
    inbox.poll_email(shop.id)
    enquiry_id = events_for_business_last_enquiry(session, shop)

    inbox.review(shop.id, enquiry_id, ReviewDecision(action="approve"))

    assert [e.event for e in events_for(session, enquiry_id)][-1] == "reply.sent"


def events_for_business_last_enquiry(session, shop):
    from glaka.models import Enquiry
    return session.query(Enquiry).filter_by(business_id=shop.id).one().id


def test_personal_data_is_not_stored(session, shop) -> None:
    entry = record(session, shop.id, "draft.created", warnings=["contact details: sam@example.com, 07700 900123"])

    assert entry.details == {"warnings": ["contact details: [email], [phone]"]}


def test_edited_event_is_detected(session, shop) -> None:
    for i in range(3):
        record(session, shop.id, "test.event", n=i)
    middle = session.query(AuditEvent).filter_by(business_id=shop.id).order_by(AuditEvent.id).all()[1]

    session.execute(update(AuditEvent).where(AuditEvent.id == middle.id).values(actor="someone-else"))
    session.expire_all()

    check = verify_chain(session, shop.id)
    assert not check.ok
    assert check.first_bad_id == middle.id
    assert check.reason == "event content was changed"


def test_deleted_event_is_detected(session, shop) -> None:
    for i in range(3):
        record(session, shop.id, "test.event", n=i)
    middle = session.query(AuditEvent).filter_by(business_id=shop.id).order_by(AuditEvent.id).all()[1]

    session.delete(middle)
    session.flush()

    check = verify_chain(session, shop.id)
    assert not check.ok
    assert "broken" in check.reason


def test_chains_are_per_business(session, shop) -> None:
    from glaka.seed import seed_business

    other = seed_business(session, "other-shop", "Other")
    record(session, shop.id, "a")
    record(session, other.id, "b")
    record(session, shop.id, "c")

    assert verify_chain(session, shop.id).ok
    assert verify_chain(session, other.id).events == 1
