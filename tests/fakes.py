"""Test doubles. FakeEmbedder needs no Ollama and is deterministic."""

import hashlib
import math

from glaka.models import EMBEDDING_DIM


def _vector(text: str) -> list[float]:
    """Bag-of-words hashed into EMBEDDING_DIM buckets, then normalised.

    Texts sharing words get similar vectors, which is enough to test the
    plumbing (ranking, scoping) without a real model.
    """
    vector = [0.0] * EMBEDDING_DIM
    for word in text.lower().split():
        bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % EMBEDDING_DIM
        vector[bucket] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class FakeEmbedder:
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return _vector(text)


class FakeChat:
    """Returns a canned reply and records what it was asked."""

    def __init__(self, reply) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def generate(self, system, user, schema):
        self.calls.append({"system": system, "user": user})
        return self.reply
