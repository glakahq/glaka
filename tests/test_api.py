"""API tests: real routing and validation, fake AI, rolled-back DB session."""

import pytest
from fastapi.testclient import TestClient

from glaka.answer import LlmAnswer
from glaka.deps import get_embedder, get_llm, get_session
from glaka.main import app
from glaka.rag import ingest_document
from glaka.seed import seed_business
from tests.fakes import FakeChat, FakeEmbedder


@pytest.fixture
def client(session):
    embedder = FakeEmbedder()
    shop = seed_business(session, "api-shop", "API Shop")
    ingest_document(session, embedder, shop.id, "prices.md", "Prices", "shower install costs £450")
    llm = FakeChat(LlmAnswer(can_answer=True, answer="£450.", cited_sources=[1]))

    app.dependency_overrides = {
        get_session: lambda: session,
        get_embedder: lambda: embedder,
        get_llm: lambda: llm,
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
