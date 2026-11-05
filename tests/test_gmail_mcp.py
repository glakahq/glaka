"""Our Gmail MCP server, exercised over the real MCP protocol (in-process), with a fake Gmail API."""

import base64
import email

import pytest

from glaka.gmail_mcp.gmail import build_reply, parse_message
from glaka.gmail_mcp.server import create_server
from glaka.mail import MailError, McpMailClient
from tests.fakes import FakeGmailApi, gmail_message

LABEL = "Label_7"
CUSTOMER = gmail_message("m1", "t1", "Sam Smith <Sam@Example.com>", "Shower price?", "How much is a shower?", [LABEL, "INBOX"])
PRIVATE = gmail_message("m2", "t2", "Bank <bank@example.com>", "Your statement", "secret", ["INBOX"])
OUR_REPLY = gmail_message("m3", "t1", "Me <me@example.com>", "Re: Shower price?", "£450", [LABEL], [("X-Glaka-Reply", "1")])


@pytest.fixture
def api():
    return FakeGmailApi([CUSTOMER, PRIVATE, OUR_REPLY])


@pytest.fixture
def client(api):
    return McpMailClient(create_server(lambda: api, "glaka"))


def test_parse_message_reads_headers_and_plain_text() -> None:
    parsed = parse_message(CUSTOMER)

    assert parsed.from_name == "Sam Smith"
    assert parsed.from_address == "sam@example.com"
    assert parsed.subject == "Shower price?"
    assert parsed.body == "How much is a shower?"
    assert parsed.message_id_header == "<m1@mail.example>"


def test_fetch_only_returns_labelled_customer_mail(client, api) -> None:
    emails = client.fetch_new()

    assert [e.id for e in emails] == ["m1"]  # not the bank email, not our own reply
    assert api.queries[0].startswith("label:glaka ")


def test_reply_is_threaded_and_marked(client, api) -> None:
    sent = client.send_reply(
        thread_id="t1", to="sam@example.com", subject="Shower price?", body="It's £450.",
        in_reply_to="<m1@mail.example>", references="",
    )

    assert sent.thread_id == "t1"
    raw, thread_id = api.sent[0]
    message = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert thread_id == "t1"
    assert message["To"] == "sam@example.com"
    assert message["Subject"] == "Re: Shower price?"
    assert message["In-Reply-To"] == "<m1@mail.example>"
    assert message["X-Glaka-Reply"] == "1"


def test_cannot_reply_in_an_unlabelled_thread(client, api) -> None:
    with pytest.raises(MailError, match="not labelled"):
        client.send_reply(thread_id="t2", to="bank@example.com", subject="x", body="hi", in_reply_to="", references="")
    assert api.sent == []


def test_cannot_reply_to_someone_outside_the_thread(client, api) -> None:
    with pytest.raises(MailError, match="did not write"):
        client.send_reply(thread_id="t1", to="stranger@example.com", subject="x", body="hi", in_reply_to="", references="")
    assert api.sent == []


def test_build_reply_rejects_header_injection() -> None:
    with pytest.raises(ValueError):
        build_reply("sam@example.com", "Hi\r\nBcc: everyone@example.com", "body", "", "")


def test_build_reply_allows_one_recipient_only() -> None:
    with pytest.raises(ValueError):
        build_reply("a@example.com, b@example.com", "Hi", "body", "", "")
