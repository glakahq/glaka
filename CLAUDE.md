# Glaka

Open-source (AGPL-3.0) AI front desk for small businesses. It drafts replies to customer enquiries from the business's own data, and the owner approves them. Public repo: https://github.com/glakahq/glaka. Homepage: https://glaka.io.

## Stack

Python · FastAPI · LangGraph · Pydantic · Postgres + pgvector · MCP (Gmail, Calendar) · Ollama for dev (`llama3.1:8b`, `nomic-embed-text`), with the model provider swappable for production. Use `uv` for project and dependency management.

## Architecture rules

- **Multi-tenant from day one:** every table, query and RAG search is scoped by `business_id`. Never hard-code a business's prices, tone or domain; they live in the DB.
- **Grounded answers only:** retrieve the business's data before calling the LLM, and return citations. Never invent prices.
- **Human in the loop:** LangGraph `interrupt()` pauses for the owner to approve, edit or reject. Auto-send is opt-in per business.
- **Safety:** input/output guardrails, prompt-injection defence, PII redaction before logging, an audit trail.
- **Evals:** a Promptfoo (or similar) test suite for assistant behaviour.

## Roadmap

0. ✅ Project setup: uv, FastAPI `/health`, pytest
1. ✅ Postgres + pgvector (Docker), a `businesses` table, first tenant seeded
2. ✅ RAG answers with citations for one business
3. ✅ Approval loop (API + LangGraph human review)
4. ✅ Email in/out via MCP
5. ✅ Guardrails + eval suite, plus a hash-chained audit trail
6. ✅ Multi-business support + dashboard
7. ✅ Embeddable website chat widget

What's built: `docs/progress.md`. Deferred work: `docs/backlog.md` (add to it when cutting a corner).

## Public repo hygiene

This repo is public. Never commit secrets (`.env`), customer data, or anything in `docs/private/` or `CLAUDE.local.md` (both are gitignored).
