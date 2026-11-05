"""Website chat widget: public endpoints (key + allowed origins + rate limits) and settings."""

import pytest
from fastapi.testclient import TestClient

from glaka import widget
from glaka.deps import get_inbox, get_session
from glaka.inbox import ReviewDecision
from glaka.main import app
from glaka.models import AuditEvent, Enquiry
from glaka.ratelimit import RateLimiter
from tests.conftest import sign_in

SITE = "https://www.test-shop.example"


@pytest.fixture
def chat_shop(session, shop):
    shop.widget_enabled = True
    shop.widget_key = widget.new_widget_key()
    shop.widget_allowed_origins = SITE
    session.flush()
    return shop


@pytest.fixture
def client(session, inbox, monkeypatch):
    monkeypatch.setattr(widget, "per_visitor", RateLimiter(limit=8, window_seconds=600))
    monkeypatch.setattr(widget, "per_business", RateLimiter(limit=300, window_seconds=86400))
    app.dependency_overrides = {get_session: lambda: session, get_inbox: lambda: inbox}
    yield TestClient(app)
    app.dependency_overrides = {}


def post(client, shop, message, origin=SITE, **extra):
    return client.post(f"/widget/{shop.widget_key}/messages", json={"message": message, **extra},
                       headers={"Origin": origin})


def test_script_is_served(client) -> None:
    response = client.get("/widget.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "data-glaka-key" in response.text


def test_config_with_cors_for_allowed_site(client, chat_shop) -> None:
    response = client.get(f"/widget/{chat_shop.widget_key}/config", headers={"Origin": SITE})
    assert response.status_code == 200
    assert response.json() == {"business_name": "Test Shop", "greeting": widget.DEFAULT_GREETING}
    assert response.headers["access-control-allow-origin"] == SITE


def test_preflight(client, chat_shop) -> None:
    response = client.options(f"/widget/{chat_shop.widget_key}/messages",
                              headers={"Origin": SITE, "Access-Control-Request-Method": "POST"})
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == SITE


def test_other_websites_are_refused(client, chat_shop) -> None:
    response = post(client, chat_shop, "shower install costs", origin="https://evil.example")
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_unknown_or_disabled_widget_is_not_found(client, chat_shop, session) -> None:
    assert client.get("/widget/wk_nope/config").status_code == 404
    chat_shop.widget_enabled = False
    session.flush()
    assert client.get(f"/widget/{chat_shop.widget_key}/config").status_code == 404


def test_message_waits_for_approval_then_shows_in_conversation(client, chat_shop, inbox, session) -> None:
    response = post(client, chat_shop, "shower install costs", email="Sam@Example.com")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "pending" and body["reply"] is None
    token = body["conversation"]

    enquiry = session.query(Enquiry).filter_by(business_id=chat_shop.id).one()
    assert enquiry.channel == "chat"
    assert enquiry.customer_email == "sam@example.com"
    assert enquiry.conversation_hash == widget.conversation_hash(token)  # only the hash is stored

    url = f"/widget/{chat_shop.widget_key}/conversations/{token}"
    assert [m["role"] for m in client.get(url, headers={"Origin": SITE}).json()] == ["visitor"]

    inbox.review(chat_shop.id, enquiry.id, ReviewDecision(action="approve"))
    messages = client.get(url, headers={"Origin": SITE}).json()
    assert [(m["role"], m["text"]) for m in messages] == [
        ("visitor", "shower install costs"), ("business", "It costs £450.")]


def test_auto_send_answers_straight_away(client, chat_shop, session) -> None:
    chat_shop.auto_send = True
    session.flush()
    body = post(client, chat_shop, "shower install costs").json()
    assert body["status"] == "answered"
    assert body["reply"] == "It costs £450."


def test_follow_up_joins_the_same_conversation(client, chat_shop, session) -> None:
    token = post(client, chat_shop, "shower install costs").json()["conversation"]
    assert post(client, chat_shop, "and a bath?", conversation=token).json()["conversation"] == token
    messages = client.get(f"/widget/{chat_shop.widget_key}/conversations/{token}").json()
    assert [m["text"] for m in messages] == ["shower install costs", "and a bath?"]


def test_conversations_are_private(client, chat_shop) -> None:
    post(client, chat_shop, "shower install costs")
    guess = "x" * 43
    assert client.get(f"/widget/{chat_shop.widget_key}/conversations/{guess}").json() == []


def test_visitor_rate_limit(client, chat_shop) -> None:
    for _ in range(8):
        assert post(client, chat_shop, "shower install costs").status_code == 200
    assert post(client, chat_shop, "shower install costs").status_code == 429


def test_bad_email_is_rejected(client, chat_shop) -> None:
    assert post(client, chat_shop, "hello", email="not-an-email").status_code == 422


# --- settings in the dashboard ------------------------------------------------------------


@pytest.fixture
def owner_client(client, owner):
    client.csrf = sign_in(client)
    return client


def test_enable_widget_creates_key_and_snippet(owner_client, shop, session) -> None:
    response = owner_client.post("/ui/test-shop/widget", data={
        "widget_enabled": "on", "widget_greeting": "Hello!",
        "widget_allowed_origins": "https://WWW.Test-Shop.example/\n", "csrf_token": owner_client.csrf})
    assert response.status_code == 200  # followed the redirect back to settings

    session.refresh(shop)
    assert shop.widget_enabled and shop.widget_key.startswith("wk_")
    assert shop.widget_allowed_origins == SITE
    assert f'data-glaka-key="{shop.widget_key}"' in response.text
    events = session.query(AuditEvent).filter_by(business_id=shop.id, event="widget.updated").all()
    assert events and "widget_key" in events[0].details["fields"]


def test_bad_origin_is_refused(owner_client, shop, session) -> None:
    response = owner_client.post("/ui/test-shop/widget", data={
        "widget_enabled": "on", "widget_allowed_origins": "https://shop.example/contact",
        "csrf_token": owner_client.csrf}, follow_redirects=False)
    assert "error=origin" in response.headers["location"]
    session.refresh(shop)
    assert not shop.widget_enabled


def test_rotate_key(owner_client, chat_shop, session) -> None:
    old = chat_shop.widget_key
    owner_client.post("/ui/test-shop/widget/rotate", data={"csrf_token": owner_client.csrf})
    session.refresh(chat_shop)
    assert chat_shop.widget_key != old
    assert owner_client.get(f"/widget/{old}/config").status_code == 404


def test_preview_page_embeds_the_widget(owner_client, chat_shop) -> None:
    page = owner_client.get("/ui/test-shop/widget/preview").text
    assert f'data-glaka-key="{chat_shop.widget_key}"' in page


def test_chat_enquiry_card(owner_client, chat_shop) -> None:
    post(owner_client, chat_shop, "shower install costs")
    page = owner_client.get("/ui/test-shop").text
    assert "website chat" in page
    assert "Approve (shows in the chat)" in page
