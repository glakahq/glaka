"""UI smoke tests: pages render and forms drive the same workflow."""

import pytest
from fastapi.testclient import TestClient

from glaka.deps import get_inbox, get_session
from glaka.main import app
from glaka.models import Enquiry


@pytest.fixture
def client(session, inbox, shop):
    app.dependency_overrides = {get_session: lambda: session, get_inbox: lambda: inbox}
    yield TestClient(app)
    app.dependency_overrides = {}


def test_business_list_page(client) -> None:
    page = client.get("/ui")
    assert page.status_code == 200
    assert "Test Shop" in page.text


def test_submit_then_edit_via_forms(client, session, shop) -> None:
    client.post("/ui/test-shop/enquiries", data={"message": "shower install costs", "customer_name": "Sam"})
    page = client.get("/ui/test-shop")
    assert "Needs review (1)" in page.text
    assert "It costs £450." in page.text

    enquiry = session.query(Enquiry).filter_by(business_id=shop.id).one()
    client.post(
        f"/ui/test-shop/enquiries/{enquiry.id}/review",
        data={"action": "send", "reply": "Hi Sam, it's £450."},
    )

    session.expire_all()
    assert session.get(Enquiry, enquiry.id).status == "edited"
    assert "Needs review (0)" in client.get("/ui/test-shop").text


def test_customer_text_is_escaped(client) -> None:
    client.post("/ui/test-shop/enquiries", data={"message": "<script>alert(1)</script>"})

    page = client.get("/ui/test-shop").text

    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_ai_failure_shows_a_message_and_marks_the_enquiry_failed(client, session, llm, shop) -> None:
    from glaka.llm import LlmError

    def broken(system, user, schema):
        raise LlmError("empty reply")

    llm.generate = broken

    response = client.post("/ui/test-shop/enquiries", data={"message": "shower install costs"})

    assert "error=ai" in str(response.url)
    assert "AI provider didn" in response.text
    assert session.query(Enquiry).filter_by(business_id=shop.id).one().status == "failed"
