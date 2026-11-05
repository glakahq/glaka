# What's built so far

A running record of each roadmap step: what it added and the main design decisions.
Things deliberately left for later are in [backlog.md](backlog.md).

## How the pieces fit

```
 customer email ──► Gmail ──(label "glaka")──► Glaka Gmail MCP server ──► poll ──┐
 web form / API ─────────────────────────────────────────────────────────────────┤
                                                                                 ▼
                              ┌──────────── LangGraph workflow (per enquiry) ───────────┐
                              │ draft: input guardrails → RAG (pgvector) → LLM (JSON)   │
                              │        → output guardrails (prices, contacts, leaks)    │
                              │ review: interrupt() ── owner approves / edits / rejects │
                              │ send:  reply via MCP in the same Gmail thread           │
                              └─────────────────────────────────────────────────────────┘
                                         │ state in Postgres (checkpointer)
                                         ▼
                    Postgres: businesses, documents, chunks (vector), enquiries
```

## Step 0: project setup
- `uv` project (src layout, Python 3.13), FastAPI `/health`, pytest.

## Step 1: database
- Postgres 17 + pgvector in Docker (host port 5433), SQLAlchemy 2 models, Alembic migrations.
- `businesses` is the tenant table; every other table carries `business_id`.
- `python -m glaka.seed --slug … --name …` creates a business (idempotent).

## Step 2: grounded answers (RAG)
- Documents are split at headings/paragraphs, embedded locally with `nomic-embed-text`
  (Ollama) and stored in `chunks.embedding` (`vector(768)`, HNSW cosine index).
- Answering: retrieve first → numbered sources in the prompt → strict JSON output
  (`can_answer`, `answer`, `cited_sources`) → validate citations → flag any £ amount
  that isn't in the cited sources. No relevant sources means no LLM call and a polite hand-over.
- Chat model via OpenRouter (default) or Ollama, chosen by `LLM_PROVIDER`; embeddings stay local.
- A fictional demo business lives in `examples/demo-bathrooms/`.

## Step 3: approval loop + inbox UI
- LangGraph workflow `draft → review (interrupt) → send | reject`, checkpointed in Postgres,
  so a draft can wait for the owner across restarts.
- Opt-in `auto_send` per business, only for grounded drafts.
- Server-rendered inbox (Jinja2) at `/ui`: review, edit, reject, see sources.

## Step 4: email via MCP
- Glaka's own Gmail MCP server (`src/glaka/gmail_mcp/`, official `mcp` SDK 2.x), two tools:
  `list_new_emails`, `send_reply`. Scopes: `gmail.readonly` + `gmail.send`.
- The server enforces: read only labelled mail, reply only in labelled threads to someone
  who wrote there, skip its own replies, reject header injection and extra recipients.
- Setup: [gmail-setup.md](gmail-setup.md). Flow diagram: [email-flow.md](email-flow.md).

## Step 5: guardrails + evals
- Retrieval threshold measured, not guessed: on-topic ≤ 0.354, off-topic ≥ 0.396 → 0.38
  (`RETRIEVAL_MAX_DISTANCE`).
- Input: automated emails (newsletter/no-reply/auto-reply headers) are stored as *ignored*
  and never sent to the AI; prompt-injection patterns are flagged.
- Output: unknown links/emails/phone numbers and leaked source numbers are flagged.
  Any warning makes a draft "not grounded": never auto-sent, owner sees why.
- Logs: every line is PII-redacted (emails, phones, cards, IBANs, UK postcodes).
- Promptfoo evals (`evals/`): 14 real-model cases (grounded answers, no invention, attacks).
  The first run found a real gap (fake `SYSTEM:` messages weren't flagged), now fixed.

## Step 5b: audit trail
- `audit_events`: append-only, hash-chained per business (each hash covers the event plus
  the previous hash). Written in the same transaction as the change it describes.
- Events: `enquiry.received`, `enquiry.ignored`, `draft.created`, `draft.failed`,
  `review.approved|edited|rejected|auto_approved`, `reply.sent`, `reply.send_failed`.
- No message text or contact details are stored; details pass through PII redaction.
- `python -m glaka.cli verify-audit --business <slug>` detects edited, deleted or
  reordered events. Each enquiry card in the UI shows its activity.

## Running it

```
docker compose up -d                      # Postgres
uv run alembic upgrade head               # schema
uv run python -m glaka.seed --slug demo-bathrooms --name "Demo Bathrooms"
uv run python -m glaka.cli ingest --business demo-bathrooms examples/demo-bathrooms/*.md
uv run uvicorn glaka.main:app --reload --port 8001   # UI at /ui, API docs at /docs
uv run pytest                             # unit + integration tests
cd evals && npm install && npm run eval   # behaviour evals (needs the app running)
```

Configuration is in `.env` (copy `.env.example`).
