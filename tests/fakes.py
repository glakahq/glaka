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


class FakeMail:
    """Stands in for the MCP mail client."""

    def __init__(self, emails=None) -> None:
        self.emails = list(emails or [])
        self.sent: list[dict] = []
        self.fail_send = False

    def fetch_new(self):
        return list(self.emails)

    def send_reply(self, **reply):
        from glaka.gmail_mcp.schemas import SentEmail
        from glaka.mail import MailError

        if self.fail_send:
            raise MailError("send_reply failed: simulated outage")
        self.sent.append(reply)
        return SentEmail(id=f"sent-{len(self.sent)}", thread_id=reply["thread_id"])


def b64(text: str) -> str:
    import base64

    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def gmail_message(message_id, thread_id, sender, subject, body, labels, extra_headers=()):
    """A message shaped like the Gmail API's format=full response."""
    headers = [
        {"name": "From", "value": sender},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": "Thu, 5 Nov 2026 10:00:00 +0000"},
        {"name": "Message-ID", "value": f"<{message_id}@mail.example>"},
        *[{"name": n, "value": v} for n, v in extra_headers],
    ]
    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": list(labels),
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": headers,
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64(body)}},
                {"mimeType": "text/html", "body": {"data": b64(f"<p>{body}</p>")}},
            ],
        },
    }


class FakeGmailApi:
    """Same methods as glaka.gmail_mcp.gmail.GoogleGmailApi, backed by a list."""

    def __init__(self, messages, labels=None) -> None:
        self.messages = {m["id"]: m for m in messages}
        self.labels = labels or {"glaka": "Label_7"}
        self.queries: list[str] = []
        self.sent: list[tuple[str, str]] = []

    def label_id(self, name):
        return self.labels.get(name)

    def search(self, query, max_results):
        self.queries.append(query)
        wanted = self.labels.get(query.split()[0].removeprefix("label:"))
        return [m["id"] for m in self.messages.values() if wanted in m["labelIds"]][:max_results]

    def get_message(self, message_id):
        return self.messages[message_id]

    def thread_info(self, thread_id):
        from email.utils import parseaddr

        in_thread = [m for m in self.messages.values() if m["threadId"] == thread_id]
        labels = {label for m in in_thread for label in m["labelIds"]}
        senders = {
            parseaddr(h["value"])[1].lower()
            for m in in_thread for h in m["payload"]["headers"] if h["name"] == "From"
        }
        return labels, senders

    def send(self, raw, thread_id):
        self.sent.append((raw, thread_id))
        return {"id": f"sent-{len(self.sent)}", "threadId": thread_id}
