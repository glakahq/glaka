"""API tests: real routing and validation, fake AI, rolled-back DB session."""

import pytest
from fastapi.testclient import TestClient

from glaka.answer import LlmAnswer
from glaka.deps import get_embedder, get_inbox, get_llm, get_session
from glaka.main import app
from glaka.rag import ingest_document
from glaka.seed import seed_business
from tests.fakes import FakeChat, FakeEmbedder


@pytest.fixture
def client(session, inbox):
    embedder = FakeEmbedder()
    shop = seed_business(session, "api-shop", "API Shop")
    ingest_document(session, embedder, shop.id, "prices.md", "Prices", "shower install costs £450")
    llm = FakeChat(LlmAnswer(can_answer=True, answer="£450.", cited_sources=[1]))

    app.dependency_overrides = {
        get_session: lambda: session,
        get_embedder: lambda: embedder,
        get_llm: lambda: llm,
        get_inbox: lambda: inbox,
    }
    yield TestClient(app)
    app.dependency_overrides = {}


def test_ask_returns_answer_with_citations(client) -> None:
    response = client.post("/businesses/api-shop/ask", json={"question": "shower install costs"})

    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is True
    assert body["citations"][0]["source"] == "prices.md"


def test_unknown_business_is_404(client) -> None:
    response = client.post("/businesses/nope/ask", json={"question": "hi"})

    assert response.status_code == 404


def test_empty_question_is_rejected(client) -> None:
    response = client.post("/businesses/api-shop/ask", json={"question": ""})

    assert response.status_code == 422


def test_provider_failure_is_a_503_without_details(client, session) -> None:
    from glaka.llm import LlmError

    class BrokenChat:
        def generate(self, system, user, schema):
            raise LlmError("OpenRouter 403: secret account details")

    app.dependency_overrides[get_llm] = lambda: BrokenChat()
    response = client.post("/businesses/api-shop/ask", json={"question": "shower install costs"})

    assert response.status_code == 503
    assert "secret" not in response.text


def test_enquiry_round_trip(client) -> None:
    created = client.post("/businesses/api-shop/enquiries", json={"message": "shower install costs"})
    assert created.status_code == 201
    enquiry = created.json()
    assert enquiry["status"] == "pending_review"

    reviewed = client.post(
        f"/businesses/api-shop/enquiries/{enquiry['id']}/review", json={"action": "approve"}
    )
    assert reviewed.json()["status"] == "approved"

    again = client.post(
        f"/businesses/api-shop/enquiries/{enquiry['id']}/review", json={"action": "approve"}
    )
    assert again.status_code == 409

    listed = client.get("/businesses/api-shop/enquiries").json()
    assert [e["id"] for e in listed] == [enquiry["id"]]


def test_enquiry_of_another_business_is_404(client, session) -> None:
    seed_business(session, "other-shop", "Other Shop")
    enquiry = client.post("/businesses/api-shop/enquiries", json={"message": "shower"}).json()

    response = client.post(
        f"/businesses/other-shop/enquiries/{enquiry['id']}/review", json={"action": "approve"}
    )

    assert response.status_code == 404
