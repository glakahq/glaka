# Glaka

**Open-source AI front desk for small businesses.**

*Glaka* (γλάκα) is Cretan dialect for **"run!"**. That's the idea: when a customer gets in touch, Glaka runs to answer, so no enquiry waits and no job is lost to a late reply. The business owner always stays in control.

> **Status: early development.** Usable for local testing; not production-ready yet.
> See [what's built](docs/progress.md) and [what's left](docs/backlog.md).

## What it will do

A tradesperson, café or shop gets an enquiry by email, website chat or (later) WhatsApp: *"How much for a bathroom refit?"*, *"Are you open Sunday?"*, *"Can I book a table for 6?"*

1. Glaka **drafts a reply** from the business's own information: prices, services, opening hours, FAQ.
2. The owner **approves, edits or rejects it with one tap**.
3. The approved reply is sent, and everything is logged.

## Design principles

- **Grounded answers.** Replies come from the business's own data (RAG), never invented. No made-up prices.
- **Human in the loop.** The AI drafts and a person decides. Auto-send is opt-in, per business.
- **Safe by default.** Input and output guardrails, prompt-injection defences, and PII redaction before logging.
- **Tested behaviour.** An evaluation suite checks the assistant's answers before every release.
- **Multi-tenant from day one.** Each business's data, settings and connections are kept separate.
- **Model-agnostic.** Local models via Ollama for development, swappable hosted models in production.

## Stack

Python · FastAPI · LangGraph · Pydantic · Postgres + pgvector · MCP integrations (Gmail, Calendar) · Ollama

## Roadmap

1. ✅ RAG answers from one business's information, with citations
2. ✅ Approval loop: API + LangGraph human review
3. ✅ Email in and out via MCP
4. ✅ Guardrails + evaluation suite
5. ✅ Multi-business support + dashboard
6. ✅ Embeddable website chat widget

## Try it locally

See [docs/progress.md](docs/progress.md#running-it). Connecting Gmail: [docs/gmail-setup.md](docs/gmail-setup.md).

## Hosted version

Glaka is free to self-host. A hosted version and done-for-you setup will be offered by [BlueTech Labs](https://bluetechlabs.co.uk).

## License

[GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0). You're free to use, modify and self-host Glaka. If you offer a modified version to others as a network service, you must share your changes under the same licence.
