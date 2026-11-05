"""Build the real Inbox (Postgres checkpointer, Ollama/OpenRouter, Gmail over MCP).

Shared by the web app (main.lifespan) and the CLI so they're wired the same way.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from glaka.config import get_settings
from glaka.db import SessionLocal
from glaka.embeddings import OllamaEmbedder
from glaka.inbox import Inbox
from glaka.llm import make_chat_model
from glaka.mail import McpMailClient, gmail_server_process
from glaka.workflow import build_workflow


def make_mail() -> McpMailClient:
    return McpMailClient(gmail_server_process())


@contextmanager
def open_inbox() -> Iterator[Inbox]:
    # The checkpointer uses plain psycopg, so drop SQLAlchemy's "+psycopg" driver tag.
    conninfo = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    with ConnectionPool(conninfo, kwargs={"autocommit": True, "row_factory": dict_row}, open=True) as pool:
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()  # creates LangGraph's own tables if missing
        workflow = build_workflow(SessionLocal.begin, OllamaEmbedder, make_chat_model, make_mail, checkpointer)
        yield Inbox(SessionLocal.begin, workflow, make_mail)
