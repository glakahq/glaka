"""Sign-in, sessions, tokens and tenant isolation."""

import pytest
from fastapi.testclient import TestClient

from glaka.auth import LoginLimiter, authenticate, create_api_token, create_user
from glaka.deps import get_inbox, get_session
from glaka.main import app
from tests.conftest import PASSWORD, csrf_from, sign_in


@pytest.fixture
def client(session, inbox, shop, owner):
    app.dependency_overrides = {get_session: lambda: session, get_inbox: lambda: inbox}
    yield TestClient(app)
    app.dependency_overrides = {}


def test_pages_need_sign_in(client) -> None:
    response = client.get("/ui/test-shop", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/ui/test-shop"


def test_sign_in_then_back_to_the_page(client) -> None:
    token = csrf_from(client.get("/login").text)
    response = client.post("/login", data={"email": "OWNER@example.com", "password": PASSWORD,
                                           "csrf_token": token, "next": "/ui/test-shop"}, follow_redirects=False)

    assert response.headers["location"] == "/ui/test-shop"
    assert client.get("/ui/test-shop").status_code == 200


def test_wrong_password_is_refused(client) -> None:
    token = csrf_from(client.get("/login").text)
    response = client.post("/login", data={"email": "owner@example.com", "password": "nope", "csrf_token": token})

    assert "Wrong email or password" in response.text
    assert client.get("/ui", follow_redirects=False).status_code == 303


def test_no_open_redirect_after_sign_in(client) -> None:
    token = csrf_from(client.get("/login").text)
    response = client.post("/login", data={"email": "owner@example.com", "password": PASSWORD,
                                           "csrf_token": token, "next": "https://evil.example"},
                           follow_redirects=False)

    assert response.headers["location"] == "/ui"


def test_sign_out(client) -> None:
    csrf = sign_in(client)

    client.post("/logout", data={"csrf_token": csrf})

    assert client.get("/ui", follow_redirects=False).status_code == 303


def test_api_needs_a_token(client) -> None:
    assert client.get("/businesses/test-shop/enquiries").status_code == 401


def test_api_token_works_only_for_my_businesses(client, session, owner) -> None:
    from glaka.seed import seed_business

    seed_business(session, "someone-else", "Someone Else")
    headers = {"Authorization": f"Bearer {create_api_token(session, owner, 'tests')}"}

    assert client.get("/businesses/test-shop/enquiries", headers=headers).status_code == 200
    assert client.get("/businesses/someone-else/enquiries", headers=headers).status_code == 404
    assert client.get("/businesses/test-shop/enquiries",
                      headers={"Authorization": "Bearer glk_wrong"}).status_code == 401


def test_admin_sees_every_business(client, session) -> None:
    from glaka.seed import seed_business

    seed_business(session, "someone-else", "Someone Else")
    create_user(session, "admin@example.com", "Ada Admin", PASSWORD, is_admin=True)
    sign_in(client, "admin@example.com")

    page = client.get("/ui").text

    assert "Someone Else" in page and "Test Shop" in page
    assert "Add a business" in page


def test_passwords_are_hashed_and_must_be_long(session) -> None:
    user = create_user(session, "x@example.com", "X", "a long password!")

    assert user.password_hash.startswith("$argon2id$")
    assert authenticate(session, "x@example.com", "a long password!") == user
    with pytest.raises(ValueError):
        create_user(session, "y@example.com", "Y", "short")


def test_login_limiter_blocks_after_repeated_failures() -> None:
    limiter = LoginLimiter(limit=3, window=60)
    for _ in range(3):
        limiter.failed("1.2.3.4", "a@example.com")

    assert limiter.blocked("1.2.3.4", "A@example.com")
    assert not limiter.blocked("5.6.7.8", "a@example.com")
    limiter.succeeded("1.2.3.4", "a@example.com")
    assert not limiter.blocked("1.2.3.4", "a@example.com")
