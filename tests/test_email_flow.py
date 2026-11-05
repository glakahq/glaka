"""Email in -> draft -> owner decision -> email out, with fake mail and AI."""

from glaka.gmail_mcp.schemas import Email
from glaka.inbox import ReviewDecision
from glaka.models import Enquiry

EMAIL = Email(
    id="m1", thread_id="t1", from_name="Sam", from_address="sam@example.com",
    subject="Shower", date="", body="shower install costs",
    message_id_header="<m1@mail.example>", references="",
)


def _enquiries(session, shop):
    session.expire_all()
    return session.query(Enquiry).filter_by(business_id=shop.id).all()


def test_poll_creates_one_enquiry_per_email(session, shop, inbox, mail) -> None:
    mail.emails = [EMAIL]

    first = inbox.poll_email(shop.id)
    second = inbox.poll_email(shop.id)

    assert (first.new, second.new, second.already_seen) == (1, 0, 1)
    [enquiry] = _enquiries(session, shop)
    assert enquiry.channel == "email"
    assert enquiry.customer_email == "sam@example.com"
    assert enquiry.status == "pending_review"


def test_approving_an_email_enquiry_replies_in_the_thread(session, shop, inbox, mail) -> None:
    mail.emails = [EMAIL]
    inbox.poll_email(shop.id)
    [enquiry] = _enquiries(session, shop)

    inbox.review(shop.id, enquiry.id, ReviewDecision(action="approve"))

    assert mail.sent == [{
        "thread_id": "t1", "to": "sam@example.com", "subject": "Shower",
        "body": "It costs £450.", "in_reply_to": "<m1@mail.example>", "references": "",
    }]
    [enquiry] = _enquiries(session, shop)
    assert enquiry.status == "approved"
    assert enquiry.sent_external_id == "sent-1"


def test_rejecting_sends_nothing(session, shop, inbox, mail) -> None:
    mail.emails = [EMAIL]
    inbox.poll_email(shop.id)
    [enquiry] = _enquiries(session, shop)

    inbox.review(shop.id, enquiry.id, ReviewDecision(action="reject"))

    assert mail.sent == []


def test_send_failure_can_be_retried(session, shop, inbox, mail) -> None:
    mail.emails = [EMAIL]
    inbox.poll_email(shop.id)
    [enquiry] = _enquiries(session, shop)
    mail.fail_send = True

    inbox.review(shop.id, enquiry.id, ReviewDecision(action="edit", reply="£450, Sam."))
    assert _enquiries(session, shop)[0].status == "send_failed"

    mail.fail_send = False
    inbox.retry_send(shop.id, enquiry.id)

    [enquiry] = _enquiries(session, shop)
    assert enquiry.status == "edited"
    assert mail.sent[0]["body"] == "£450, Sam."


def test_web_enquiries_send_no_email(session, shop, inbox, mail) -> None:
    enquiry_id = inbox.submit(shop.id, "shower install costs")

    inbox.review(shop.id, enquiry_id, ReviewDecision(action="approve"))

    assert mail.sent == []


def test_automated_emails_are_stored_but_not_drafted(session, shop, inbox, mail, llm) -> None:
    mail.emails = [EMAIL.model_copy(update={"id": "news1", "automated_reason": "mailing list or newsletter"})]

    result = inbox.poll_email(shop.id)

    assert (result.new, result.ignored) == (0, 1)
    [enquiry] = _enquiries(session, shop)
    assert enquiry.status == "ignored"
    assert enquiry.warnings == ["not drafted: mailing list or newsletter"]
    assert llm.calls == []  # no AI call, so nothing leaves the building
    assert inbox.poll_email(shop.id).already_seen == 1
