"""UI tests: signed in through the real login form, forms carry the real CSRF token."""

import pytest
from fastapi.testclient import TestClient

from glaka.deps import get_embedder, get_inbox, get_session
from glaka.main import app
from glaka.models import AuditEvent, Business, Document, Enquiry
from tests.conftest import sign_in
from tests.fakes import FakeEmbedder


@pytest.fixture
def client(session, inbox, shop, owner):
    app.dependency_overrides = {
        get_session: lambda: session,
        get_inbox: lambda: inbox,
        get_embedder: lambda: FakeEmbedder(),
    }
    c = TestClient(app)  # not `with`: that would run startup (real DB pool, email poller)
    c.csrf = sign_in(c)
    yield c
    app.dependency_overrides = {}


def test_business_list_shows_only_my_businesses(client, session) -> None:
    from glaka.seed import seed_business

    seed_business(session, "someone-else", "Someone Else Ltd")

    page = client.get("/ui").text

    assert "Test Shop" in page
    assert "Someone Else Ltd" not in page


def test_other_businesses_are_not_found(client, session) -> None:
    from glaka.seed import seed_business

    seed_business(session, "someone-else", "Someone Else Ltd")

    assert client.get("/ui/someone-else").status_code == 404
    assert client.get("/ui/someone-else/knowledge").status_code == 404


def test_submit_then_edit_via_forms(client, session, shop) -> None:
    client.post("/ui/test-shop/enquiries",
                data={"message": "shower install costs", "customer_name": "Sam", "csrf_token": client.csrf})
    page = client.get("/ui/test-shop")
    assert "Needs review (1)" in page.text
    assert "It costs £450." in page.text

    enquiry = session.query(Enquiry).filter_by(business_id=shop.id).one()
    client.post(f"/ui/test-shop/enquiries/{enquiry.id}/review",
                data={"action": "send", "reply": "Hi Sam, it's £450.", "csrf_token": client.csrf})

    session.expire_all()
    assert session.get(Enquiry, enquiry.id).status == "edited"
    assert "Needs review (0)" in client.get("/ui/test-shop").text


def test_review_is_attributed_to_the_signed_in_user(client, session, shop, owner) -> None:
    client.post("/ui/test-shop/enquiries", data={"message": "shower install costs", "csrf_token": client.csrf})
    enquiry = session.query(Enquiry).filter_by(business_id=shop.id).one()

    client.post(f"/ui/test-shop/enquiries/{enquiry.id}/review",
                data={"action": "reject", "csrf_token": client.csrf})

    events = session.query(AuditEvent).filter_by(enquiry_id=enquiry.id).all()
    assert events[-1].actor == f"user:{owner.id}"
    assert "Olive Owner" in client.get("/ui/test-shop/activity").text


def test_forms_without_csrf_token_are_refused(client, session, shop) -> None:
    response = client.post("/ui/test-shop/enquiries", data={"message": "shower install costs"})

    assert response.status_code == 403
    assert session.query(Enquiry).filter_by(business_id=shop.id).count() == 0


def test_customer_text_is_escaped(client) -> None:
    client.post("/ui/test-shop/enquiries", data={"message": "<script>alert(1)</script>", "csrf_token": client.csrf})

    page = client.get("/ui/test-shop").text

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_ai_failure_shows_a_message_and_marks_the_enquiry_failed(client, session, llm, shop) -> None:
    from glaka.llm import LlmError

    def broken(system, user, schema):
        raise LlmError("empty reply")

    llm.generate = broken

    response = client.post("/ui/test-shop/enquiries", data={"message": "shower install costs", "csrf_token": client.csrf})

    assert "error=ai" in str(response.url)
    assert "AI provider didn" in response.text
    assert session.query(Enquiry).filter_by(business_id=shop.id).one().status == "failed"


def test_add_and_remove_knowledge(client, session, shop) -> None:
    response = client.post(
        "/ui/test-shop/knowledge",
        data={"title": "Opening hours", "text": "Mon to Fri 8 till 6", "csrf_token": client.csrf},
        files={"files": ("faq.md", b"# FAQ\n\nWe cover 25 miles.", "text/markdown")},
    )
    assert "Added to the knowledge base" in response.text
    sources = {d.source for d in session.query(Document).filter_by(business_id=shop.id)}
    assert {"faq.md", "opening-hours.md"} <= sources

    doc = session.query(Document).filter_by(business_id=shop.id, source="faq.md").one()
    client.post(f"/ui/test-shop/knowledge/{doc.id}/delete", data={"csrf_token": client.csrf})
    assert "faq.md" not in {d.source for d in session.query(Document).filter_by(business_id=shop.id)}


def test_only_text_files_can_be_uploaded(client) -> None:
    response = client.post(
        "/ui/test-shop/knowledge",
        data={"csrf_token": client.csrf},
        files={"files": ("prices.exe", b"MZ...", "application/octet-stream")},
    )

    assert "Only .md and .txt files" in response.text


def test_settings_are_saved_and_audited(client, session, shop) -> None:
    client.post("/ui/test-shop/settings", data={
        "name": "Test Shop Ltd", "email_label": "test-shop", "reply_instructions": "Sign off as Olive.",
        "auto_send": "on", "csrf_token": client.csrf,
    })

    session.expire_all()
    business = session.get(Business, shop.id)
    assert (business.name, business.email_label, business.auto_send) == ("Test Shop Ltd", "test-shop", True)
    event = session.query(AuditEvent).filter_by(business_id=shop.id, event="settings.updated").one()
    assert event.details == {"fields": ["auto_send", "email_label", "name", "reply_instructions"]}


def test_only_admins_create_businesses(client) -> None:
    response = client.post("/ui/businesses", data={"slug": "new-co", "name": "New Co", "csrf_token": client.csrf})

    assert response.status_code == 403
