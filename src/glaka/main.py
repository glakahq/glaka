import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from glaka import api, ui, widget
from glaka.auth import NotAuthenticated
from glaka.config import get_settings
from glaka.llm import LlmError
from glaka.logs import setup_logging
from glaka.runtime import EmailPoller, open_inbox

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown (like @PostConstruct/@PreDestroy on the application)."""
    setup_logging()  # after uvicorn has set up its handlers, so they get redaction too
    if settings.secret_key is None:
        logger.warning("SECRET_KEY is not set: using a random key, so restarts sign everyone out")
    with open_inbox() as inbox:
        app.state.inbox = inbox
        poller = EmailPoller(inbox, settings.email_poll_seconds)
        poller.start()
        try:
            yield
        finally:
            poller.stop()


app = FastAPI(title="Glaka", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key.get_secret_value() if settings.secret_key else secrets.token_urlsafe(48),
    session_cookie="glaka_session",
    same_site="lax",  # cookies aren't sent on cross-site POSTs (CSRF defence, with the form token)
    https_only=settings.cookie_secure,
    max_age=14 * 24 * 3600,
)
app.include_router(api.router)
app.include_router(ui.router)
app.include_router(widget.router)


@app.exception_handler(NotAuthenticated)
def not_authenticated(request: Request, exc: NotAuthenticated):
    if request.url.path.startswith("/ui"):
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
    return JSONResponse(status_code=401, content={"detail": "Sign in or send an API token"})


@app.exception_handler(LlmError)
def llm_unavailable(request: Request, exc: LlmError) -> JSONResponse:
    """Provider failures become a 503; the details go to the log, not to the caller."""
    logger.error("LLM provider error: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "AI provider unavailable, try again later"})
