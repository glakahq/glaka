import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from glaka import api, ui
from glaka.llm import LlmError
from glaka.logs import setup_logging
from glaka.runtime import open_inbox

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown (like @PostConstruct/@PreDestroy on the application)."""
    setup_logging()  # after uvicorn has set up its handlers, so they get redaction too
    with open_inbox() as inbox:
        app.state.inbox = inbox
        yield


app = FastAPI(title="Glaka", lifespan=lifespan)
app.include_router(api.router)
app.include_router(ui.router)


@app.exception_handler(LlmError)
def llm_unavailable(request: Request, exc: LlmError) -> JSONResponse:
    """Provider failures become a 503; the details go to the log, not to the caller."""
    logger.error("LLM provider error: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "AI provider unavailable, try again later"})
