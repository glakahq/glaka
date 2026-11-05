"""Turn text into vectors. The Embedder protocol keeps the provider swappable."""

from typing import Protocol

import httpx

from glaka.config import get_settings


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class OllamaEmbedder:
    """nomic-embed-text via Ollama's /api/embed.

    nomic models expect a task prefix: "search_document: " for stored text and
    "search_query: " for questions. Without them retrieval quality drops.
    """

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.base_url = base_url or settings.ollama_url
        self.model = model or settings.embedding_model

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        response = httpx.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": inputs},
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["embeddings"]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([f"search_document: {t}" for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([f"search_query: {text}"])[0]
