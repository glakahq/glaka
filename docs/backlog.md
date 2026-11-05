# Backlog: things to come back to

Known gaps and deliberate shortcuts, grouped by area. Each item says why it matters.
Tick items off (or delete them) as they're done.

## Before real customer data goes through Glaka
- [ ] **Switch to a paid chat model** with `OPENROUTER_ALLOW_DATA_COLLECTION=false`.
  Free `:free` models may train on prompts; only test data may use them.
- [ ] **Google OAuth verification.** While the Google app is in *Testing*, sign-ins expire
  every 7 days and only listed test users can connect. `gmail.readonly` is a *restricted*
  scope: a public app needs Google verification and a security assessment (CASA).
- [ ] **Send idempotency.** If an email is sent but the database update then fails, a retry
  could send it twice. Record "sending" before the call, or check the thread first.
- [ ] **Concurrent reviews.** Two people approving the same draft at the same moment could
  both resume the workflow. Lock the enquiry row (`SELECT … FOR UPDATE`) during review.

## Email
- [ ] **Follow-ups in a thread** become new, separate enquiries without the earlier
  conversation, and quoted text (`> …`) isn't stripped from the body.
- [ ] **Push instead of polling** (Gmail watch + Pub/Sub) for instant pickup.
- [ ] **Smarter "is this an enquiry?"**: header checks catch newsletters and no-reply mail;
  an AI classifier may be needed for real inboxes (invoices, spam, personal mail).
- [ ] **Keep one MCP session open** instead of starting the server process per call (~1s each).
- [ ] **Other providers**: Outlook/Microsoft 365, plain IMAP/SMTP.

## Answer quality
- [ ] **Hybrid retrieval** (BM25 keyword + vector) and reranking.
- [ ] **Re-measure the retrieval threshold** per embedding model, and ideally per business.
- [ ] **Richer ingestion**: PDFs, web pages, price spreadsheets; smarter chunking.
- [ ] **Reply language and tone per business** (British English is currently the default).

## Testing and evals
- [ ] **Separate test database.** Tests roll back their changes but run against the dev DB.
- [ ] **Run evals in CI**, and add an LLM-graded rubric for tone and helpfulness.
- [ ] **More attack cases** (multilingual injections, long-context hiding, encoded payloads).
- [ ] Starlette warns that its test client should move from `httpx` to `httpx2`.

## Product
- [ ] **Retry button for failed drafts** (today: resubmit the enquiry).
- [ ] **Local time** in the UI (timestamps are shown in UTC).
- [ ] **Calendar MCP**: let customers book a survey or table.
- [ ] **More channels**: WhatsApp, SMS.
- [ ] **Self-serve sign-up and billing** (businesses are onboarded by hand for now).
