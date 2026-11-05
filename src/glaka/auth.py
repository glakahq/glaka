"""Sign-in, sessions, API tokens, CSRF and access checks (≈ Spring Security, by hand).

- Browser: signed session cookie (Starlette SessionMiddleware) + a CSRF token in every form.
- Scripts/evals: `Authorization: Bearer glk_...` API tokens; only their SHA-256 is stored.
- Access: a user sees a business only through a Membership (admins see all). A business
  the user can't access is reported as "not found", so its existence isn't leaked.
"""

import hashlib
import secrets
import time
import uuid
from collections import defaultdict, deque
from threading import Lock
from fastapi import HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from glaka.models import ApiToken, Business, Membership, User

MIN_PASSWORD_LENGTH = 10
_hasher = PasswordHash.recommended()  # Argon2id
_DUMMY_HASH = _hasher.hash("not-a-real-password")


class NotAuthenticated(Exception):
    """No valid session or token. UI routes redirect to /login; the API returns 401."""


# --- accounts -------------------------------------------------------------------------


def create_user(session: Session, email: str, name: str, password: str, is_admin: bool = False) -> User:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    user = User(email=email.strip().lower(), name=name.strip(), password_hash=_hasher.hash(password), is_admin=is_admin)
    session.add(user)
    session.flush()
    return user


def add_member(session: Session, user: User, business: Business, role: str = "owner") -> None:
    if session.get(Membership, (user.id, business.id)) is None:
        session.add(Membership(user_id=user.id, business_id=business.id, role=role))
        session.flush()


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        _hasher.verify(password, _DUMMY_HASH)  # same work either way: no "does this email exist?" timing leak
        return None
    return user if _hasher.verify(password, user.password_hash) else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_api_token(session: Session, user: User, name: str) -> str:
    """Return a new token. It is shown once; only its hash is kept."""
    token = "glk_" + secrets.token_urlsafe(32)
    session.add(ApiToken(user_id=user.id, name=name, token_hash=_token_hash(token)))
    session.flush()
    return token


# --- who is calling? --------------------------------------------------------------------


def load_user(request: Request, session: Session) -> User:
    """The signed-in user (session cookie) or the owner of a Bearer token. The FastAPI
    dependency wrapping this is glaka.deps.current_user."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = session.scalar(select(ApiToken).where(ApiToken.token_hash == _token_hash(header[7:].strip())))
        user = session.get(User, token.user_id) if token else None
    else:
        user_id = request.session.get("user_id")
        user = session.get(User, uuid.UUID(user_id)) if user_id else None
    if user is None:
        raise NotAuthenticated()
    return user


def accessible_businesses(session: Session, user: User) -> list[Business]:
    query = select(Business).order_by(Business.name)
    if not user.is_admin:
        query = query.join(Membership, Membership.business_id == Business.id).where(Membership.user_id == user.id)
    return list(session.scalars(query))


def can_access(session: Session, user: User, business: Business) -> bool:
    return user.is_admin or session.get(Membership, (user.id, business.id)) is not None


# --- sessions and CSRF ------------------------------------------------------------------


def log_in(request: Request, user: User) -> None:
    request.session.clear()  # new session on sign-in (no session fixation)
    request.session["user_id"] = str(user.id)
    request.session["csrf"] = secrets.token_urlsafe(32)


def log_out(request: Request) -> None:
    request.session.clear()


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


async def verify_csrf(request: Request) -> None:
    """Every state-changing form must echo the session's CSRF token."""
    form = await request.form()
    sent = str(form.get("csrf_token", ""))
    expected = request.session.get("csrf", "")
    if not expected or not secrets.compare_digest(sent, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token. Reload the page and try again.")


# --- brute-force protection -------------------------------------------------------------


class LoginLimiter:
    """At most `limit` failed sign-ins per (IP, email) in `window` seconds. In memory, per process."""

    def __init__(self, limit: int = 5, window: float = 15 * 60) -> None:
        self.limit, self.window = limit, window
        self._failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _recent(self, key: tuple[str, str]) -> deque[float]:
        failures = self._failures[key]
        while failures and failures[0] < time.monotonic() - self.window:
            failures.popleft()
        return failures

    def blocked(self, ip: str, email: str) -> bool:
        with self._lock:
            return len(self._recent((ip, email.lower()))) >= self.limit

    def failed(self, ip: str, email: str) -> None:
        with self._lock:
            self._recent((ip, email.lower())).append(time.monotonic())

    def succeeded(self, ip: str, email: str) -> None:
        with self._lock:
            self._failures.pop((ip, email.lower()), None)


login_limiter = LoginLimiter()
