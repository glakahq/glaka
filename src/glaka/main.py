import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from glaka import api, ui
from glaka.config import get_settings
from glaka.db import SessionLocal
from glaka.deps import get_embedder, get_llm
from glaka.inbox import Inbox
from glaka.llm import LlmError
from glaka.workflow import build_workflow

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown (like @PostConstruct/@PreDestroy on the application)."""
    # The checkpointer uses plain psycopg, so drop SQLAlchemy's "+psycopg" driver tag.
    conninfo = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    with ConnectionPool(
        conninfo, kwargs={"autocommit": True, "row_factory": dict_row}, open=True
    ) as pool:
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()  # creates LangGraph's own tables if missing
        workflow = build_workflow(SessionLocal.begin, get_embedder, get_llm, checkpointer)
        app.state.inbox = Inbox(SessionLocal.begin, workflow)
        yield


app = FastAPI(title="Glaka", lifespan=lifespan)
app.include_router(api.router)
app.include_router(ui.router)


@app.exception_handler(LlmError)
def llm_unavailable(request: Request, exc: LlmError) -> JSONResponse:
    """Provider failures become a 503; the details go to the log, not to the caller."""
    logger.error("LLM provider error: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "AI provider unavailable, try again later"})
