"""Build the real Inbox (Postgres checkpointer, Ollama/OpenRouter, Gmail over MCP).

Shared by the web app (main.lifespan) and the CLI so they're wired the same way.
"""

import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy import select

from glaka.config import get_settings
from glaka.db import SessionLocal
from glaka.embeddings import OllamaEmbedder
from glaka.inbox import Inbox
from glaka.llm import LlmError, make_chat_model
from glaka.mail import MailError, McpMailClient, gmail_server_process
from glaka.models import Business
from glaka.workflow import build_workflow

logger = logging.getLogger(__name__)


def gmail_connected(business_id: uuid.UUID) -> bool:
    return get_settings().gmail_token_file(business_id).exists()


def make_mail(business_id: uuid.UUID) -> McpMailClient:
    """A mail client for one business: its own token and its own label."""
    with SessionLocal() as session:
        business = session.get(Business, business_id)
        label = business.email_label if business else "glaka"
    return McpMailClient(gmail_server_process(get_settings().gmail_token_file(business_id), label))


@contextmanager
def open_inbox() -> Iterator[Inbox]:
    # The checkpointer uses plain psycopg, so drop SQLAlchemy's "+psycopg" driver tag.
    conninfo = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    with ConnectionPool(conninfo, kwargs={"autocommit": True, "row_factory": dict_row}, open=True) as pool:
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()  # creates LangGraph's own tables if missing
        workflow = build_workflow(SessionLocal.begin, OllamaEmbedder, make_chat_model, make_mail, checkpointer)
        yield Inbox(SessionLocal.begin, workflow, make_mail)


class EmailPoller:
    """Background thread: every N seconds, check the mailbox of each connected business."""

    def __init__(self, inbox: Inbox, interval: int) -> None:
        self.inbox, self.interval = inbox, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="email-poller", daemon=True)

    def start(self) -> None:
        if self.interval > 0:
            self._thread.start()
            logger.info("Email poller started (every %ss)", self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)

    def poll_once(self) -> None:
        with SessionLocal() as session:
            business_ids = list(session.scalars(select(Business.id)))
        for business_id in business_ids:
            if self._stop.is_set() or not gmail_connected(business_id):
                continue
            try:
                result = self.inbox.poll_email(business_id)
                if result.new or result.ignored or result.failed:
                    logger.info("Email for %s: %s", business_id, result)
            except (MailError, LlmError) as exc:
                logger.warning("Email check for %s failed: %s", business_id, exc)
            except Exception:
                logger.exception("Email check for %s crashed", business_id)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.poll_once()
