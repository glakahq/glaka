# How email flows through Glaka

From a customer's email to the owner's approved reply. Setup steps are in
[gmail-setup.md](gmail-setup.md).

```
                         ┌──────────────────────────┐
                         │        CUSTOMER          │
                         │ (while testing: you,     │
                         │  from another account)   │
                         └────────────┬─────────────┘
                                      │ 1. sends email
                                      ▼
   TESTING:  you+glaka@gmail.com
   LIVE:     the business address, e.g. hello@business.example
                                      │
┌─────────────────────────────────────┼──────────────────────────────────────┐
│ GMAIL                               ▼                                      │
│                    ┌──────────────────────────────┐                        │
│                    │ FILTER: sent to the Glaka    │                        │
│                    │ address?                     │                        │
│                    └───────┬──────────────┬───────┘                        │
│                        yes │              │ no                             │
│                            ▼              ▼                                │
│              ┌──────────────────┐   ┌──────────────────────────┐           │
│              │ label: "glaka"   │   │ normal inbox mail        │           │
│              │                  │   │ (bank, friends, spam...) │           │
│              └────────┬─────────┘   │ Glaka can NEVER see this │           │
│                       │             └──────────────────────────┘           │
└───────────────────────┼────────────────────────────────────────────────────┘
                        │ 2. "Check email now" (or poll every minute)
                        ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ GLAKA'S GMAIL MCP SERVER  (the gatekeeper: our code, 2 tools only)         │
│                                                                            │
│   list_new_emails  → only returns mail labelled "glaka"                    │
│   send_reply       → only replies in a "glaka" thread, only to the sender  │
└───────────────────────┬────────────────────────────────────────────────────┘
                        │ 3. new email becomes an enquiry
                        ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ GLAKA                                                                      │
│                                                                            │
│   4. find relevant facts in the business's documents (RAG)                 │
│        "Walk-in shower enclosure ... from £1,600"   [services-and-prices]  │
│                        │                                                   │
│                        ▼                                                   │
│   5. AI drafts a reply using ONLY those facts, with sources                │
│        "A walk-in shower starts from £1,600."                              │
│                        │                                                   │
│                        ▼                                                   │
│   6. checks: prices really in the sources? sources cited?                  │
│        grounded ✅  or  check carefully ⚠                                   │
│                        │                                                   │
│                        ▼                                                   │
│   7. PAUSE: waits for the owner (LangGraph interrupt)                      │
└───────────────────────┬────────────────────────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ THE OWNER, in the inbox UI (/ui/<business>)                                │
│                                                                            │
│   ┌──────────────────────────────────────────────┐                         │
│   │ Sam <sam@example.com>        [email] Shower  │                         │
│   │ "Hi, how much would a walk-in shower cost?"  │                         │
│   │ Draft: "A walk-in shower starts from £1,600" │                         │
│   │ [Approve and send email]  [Reject]           │                         │
│   └──────────────────────────────────────────────┘                         │
│        │ approve / edit              │ reject                              │
└────────┼─────────────────────────────┼─────────────────────────────────────┘
         │                             ▼
         │                       nothing is sent
         ▼
   8. send_reply via the MCP server → Gmail sends it from the business
      address, in the same thread ("Re: Shower")
         │
         ▼
┌──────────────────────────┐
│ CUSTOMER gets the reply  │
│ in their inbox           │
└──────────────────────────┘
```

## What each piece is for

| Piece | Purpose |
|---|---|
| **Filter + label** | Decides which emails Glaka is *allowed* to see |
| **MCP server** | Enforces that rule in code, and is the only route to Gmail |
| **RAG + checks** | The reply uses the business's real facts, never invented prices |
| **Pause for the owner** | Nothing reaches a customer without approval (unless the business opts in to auto-send for grounded drafts) |
| **Same thread** | The customer sees a normal reply to their own email |

## Testing vs live

Only the address at the top changes, plus two settings:

- **Testing:** the filter matches `you+glaka@gmail.com`. Gmail delivers `+anything`
  to your normal inbox, so only mail you deliberately send there is labelled.
- **Live:** the filter matches the business's real address. Before that, switch
  to a paid AI model with `OPENROUTER_ALLOW_DATA_COLLECTION=false`, so customer
  emails are never used for training, and screen out non-enquiries (newsletters,
  invoices) in the filter or in Glaka.

Everything below the filter stays the same.
