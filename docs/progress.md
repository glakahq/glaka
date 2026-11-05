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

## Step 6: multi-business dashboard
- **Accounts**: `users` (Argon2id password hashes), `memberships` (user ↔ business, role),
  admins see and create every business. Created from the CLI (`create-user`, `add-member`).
- **Sessions**: signed cookie (SameSite=Lax), new session on sign-in, CSRF token on every
  form, failed sign-ins rate-limited, no open redirects after sign-in.
- **API**: same rules via `Authorization: Bearer glk_…` tokens (`create-token`; only the
  SHA-256 is stored). A business you can't access is a 404, never a 403.
- **Dashboard** (`/ui/<business>`): Inbox · Knowledge (upload .md/.txt or paste; replace;
  remove) · Settings (name, reply style, auto-send, Gmail label, members) · Activity
  (audit trail with a live chain check). Reviews are attributed to the signed-in user.
- **Per-business email**: one Gmail token per business (`connect-gmail --business …`), its
  own label, and a background poller (`EMAIL_POLL_SECONDS`, default 60).
- **Per-business reply style** (`reply_instructions`) goes into the prompt after the safety
  rules, so it can change tone but not the rules.

## Step 7: website chat widget
- One script tag on the business's site: `<script src="…/widget.js" data-glaka-key="wk_…" async>`.
  Vanilla JS in a Shadow DOM (the site's CSS can't break it), text only (no HTML injection).
- Chat messages become normal enquiries (channel `chat`): same RAG, guardrails, approval
  and audit. The visitor sees the reply once it's approved, or instantly with auto-send.
- **Public, so protected differently**: a rotatable widget key, a list of websites allowed
  to embed it (CORS + Origin check), rate limits per visitor and per business, and a
  private conversation token held only by the visitor's browser (we store its SHA-256).
- Dashboard → Settings: enable, greeting, allowed websites, copy the snippet, new key,
  and a "Try it" preview page.

## Running it

```
docker compose up -d                      # Postgres
uv run alembic upgrade head               # schema
uv run python -m glaka.seed --slug demo-bathrooms --name "Demo Bathrooms"
uv run python -m glaka.cli ingest --business demo-bathrooms examples/demo-bathrooms/*.md
uv run python -m glaka.cli create-user --email you@example.com --name "You" --admin
uv run python -m glaka.cli add-member --email you@example.com --business demo-bathrooms
uv run uvicorn glaka.main:app --reload --port 8001   # sign in at /login, API docs at /docs
uv run pytest                             # unit + integration tests
cd evals && npm install && npm run eval   # behaviour evals (app running + GLAKA_TOKEN in evals/.env)
```

Configuration is in `.env` (copy `.env.example`).
