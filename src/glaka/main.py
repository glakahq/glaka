import logging
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from glaka.answer import Answer, answer_question
from glaka.deps import get_embedder, get_llm, get_session
from glaka.embeddings import Embedder
from glaka.llm import ChatModel, LlmError
from glaka.models import Business

logger = logging.getLogger(__name__)

app = FastAPI(title="Glaka")


@app.exception_handler(LlmError)
def llm_unavailable(request: Request, exc: LlmError) -> JSONResponse:
    """Provider failures become a 503; the details go to the log, not to the caller."""
    logger.error("LLM provider error: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "AI provider unavailable, try again later"})


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check: returns 200 if the process is up."""
    return {"status": "ok"}


class AskRequest(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=2000,
        description="The customer's message",
        examples=["How much is a walk-in shower?"],
    )


@app.post("/businesses/{slug}/ask")
def ask(
    slug: Annotated[
        str, Path(description="The business's short id", examples=["demo-bathrooms"])
    ],
    request: AskRequest,
    session: Annotated[Session, Depends(get_session)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    llm: Annotated[ChatModel, Depends(get_llm)],
) -> Answer:
    """Draft a grounded reply to a customer question, with citations."""
    business = session.scalar(select(Business).where(Business.slug == slug))
    if business is None:
        raise HTTPException(status_code=404, detail="Business not found")
    return answer_question(session, embedder, llm, business.id, business.name, request.question)
