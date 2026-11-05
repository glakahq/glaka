"""Thin wrapper over the Gmail API, plus pure helpers for parsing and building emails.

The wrapper is small on purpose: tests swap it for a fake with the same methods.
"""

import base64
import html
import re
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import Any, Protocol

from glaka.gmail_mcp.schemas import Email
from glaka.guardrails import automated_email_reason

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

GLAKA_HEADER = "X-Glaka-Reply"  # marks our own replies so we never answer ourselves


class GmailApi(Protocol):
    def label_id(self, name: str) -> str | None: ...
    def search(self, query: str, max_results: int) -> list[str]: ...
    def get_message(self, message_id: str) -> dict[str, Any]: ...
    def thread_info(self, thread_id: str) -> tuple[set[str], set[str]]:
        """(label ids on any message, sender addresses) for a thread."""
        ...
    def send(self, raw: str, thread_id: str) -> dict[str, Any]: ...


class GoogleGmailApi:
    def __init__(self, service: Any) -> None:
        self.users = service.users()

    def label_id(self, name: str) -> str | None:
        labels = self.users.labels().list(userId="me").execute().get("labels", [])
        return next((label["id"] for label in labels if label["name"].lower() == name.lower()), None)

    def search(self, query: str, max_results: int) -> list[str]:
        result = self.users.messages().list(userId="me", q=query, maxResults=max_results).execute()
        return [m["id"] for m in result.get("messages", [])]

    def get_message(self, message_id: str) -> dict[str, Any]:
        return self.users.messages().get(userId="me", id=message_id, format="full").execute()

    def thread_info(self, thread_id: str) -> tuple[set[str], set[str]]:
        thread = self.users.threads().get(
            userId="me", id=thread_id, format="metadata", metadataHeaders=["From"]
        ).execute()
        labels: set[str] = set()
        senders: set[str] = set()
        for message in thread.get("messages", []):
            labels.update(message.get("labelIds", []))
            sender = _header(message.get("payload", {}), "From")
            if sender:
                senders.add(parseaddr(sender)[1].lower())
        return labels, senders

    def send(self, raw: str, thread_id: str) -> dict[str, Any]:
        return self.users.messages().send(userId="me", body={"raw": raw, "threadId": thread_id}).execute()


# --- pure helpers -----------------------------------------------------------------


def _header(payload: dict[str, Any], name: str) -> str:
    for header in payload.get("headers", []):
        if header["name"].lower() == name.lower():
            return header["value"]
    return ""


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _find_body(payload: dict[str, Any], mime_type: str) -> str | None:
    if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    for part in payload.get("parts", []) or []:
        found = _find_body(part, mime_type)
        if found is not None:
            return found
    return None


def _html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", markup)
    return html.unescape(re.sub(r"<[^>]+>", "", markup)).strip()


def is_glaka_reply(message: dict[str, Any]) -> bool:
    return bool(_header(message.get("payload", {}), GLAKA_HEADER))


def parse_message(message: dict[str, Any]) -> Email:
    payload = message.get("payload", {})
    name, address = parseaddr(_header(payload, "From"))
    body = _find_body(payload, "text/plain")
    if body is None:
        body = _html_to_text(_find_body(payload, "text/html") or "")
    return Email(
        id=message["id"],
        thread_id=message["threadId"],
        from_name=name,
        from_address=address.lower(),
        subject=_header(payload, "Subject"),
        date=_header(payload, "Date"),
        body=body.strip(),
        message_id_header=_header(payload, "Message-ID"),
        references=_header(payload, "References"),
        automated_reason=automated_email_reason(
            {h["name"].lower(): h["value"] for h in payload.get("headers", [])}, address
        ),
    )


def build_reply(to: str, subject: str, body: str, in_reply_to: str, references: str) -> str:
    """A threaded reply as base64url RFC 822, ready for messages.send."""
    for value in (to, subject, in_reply_to, references):
        if "\r" in value or "\n" in value:
            raise ValueError("header values must not contain line breaks")
    if len(getaddresses([to])) != 1 or "@" not in to:
        raise ValueError("exactly one recipient address is allowed")
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}".strip()
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = f"{references} {in_reply_to}".strip()
    message[GLAKA_HEADER] = "1"
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()
