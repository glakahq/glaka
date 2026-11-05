"""RAG plumbing tests with a fake embedder. Need Postgres, not Ollama."""

from glaka.rag import ingest_document, search
from glaka.seed import seed_business
from tests.fakes import FakeEmbedder

embedder = FakeEmbedder()


def test_search_finds_the_relevant_chunk(session) -> None:
    shop = seed_business(session, "test-shop", "Test Shop")
    text = "# Prices\n\nshower install costs 450\n\n## Hours\n\nopen monday to friday"
    ingest_document(session, embedder, shop.id, "info.md", "Info", text)

    hits = search(session, embedder, shop.id, "how much does a shower install cost")

    assert hits[0].source == "info.md"
    assert "shower" in hits[0].content


def test_search_never_returns_another_business_data(session) -> None:
    shop_a = seed_business(session, "test-shop-a", "Shop A")
    shop_b = seed_business(session, "test-shop-b", "Shop B")
    ingest_document(session, embedder, shop_a.id, "a.md", "A", "shower install costs 450")
    ingest_document(session, embedder, shop_b.id, "b.md", "B", "shower install costs 999")

    hits = search(session, embedder, shop_a.id, "shower install cost", limit=10)

    assert [h.source for h in hits] == ["a.md"]


def test_reingesting_a_source_replaces_it(session) -> None:
    shop = seed_business(session, "test-shop", "Test Shop")
    ingest_document(session, embedder, shop.id, "p.md", "P", "old price 100")
    ingest_document(session, embedder, shop.id, "p.md", "P", "new price 200")

    hits = search(session, embedder, shop.id, "price", limit=10)

    assert [h.content for h in hits] == ["new price 200"]
