# Connecting Gmail (development)

Glaka reads and answers email through its own small MCP server
(`src/glaka/gmail_mcp/`). It asks Google for two permissions only: read mail
(`gmail.readonly`) and send mail (`gmail.send`). The server itself limits what
Glaka can do:

- it only reads messages that carry one Gmail label (default `glaka`);
- it only replies inside a labelled thread, to someone who wrote in that thread;
- it never answers its own replies (they carry an `X-Glaka-Reply` header).

So the label is the gate. Only mail you deliberately route to that label ever
reaches the AI. The whole journey is drawn in [email-flow.md](email-flow.md).

## 1. A Glaka address and label in Gmail

Gmail ignores anything after a `+` in your address, so `you+glaka@gmail.com`
already reaches your inbox.

1. In Gmail, open **Settings → Filters and blocked addresses → Create a new filter**.
2. **To:** `you+glaka@gmail.com` (use your own address), then **Create filter**.
3. Tick **Apply the label** → **New label** → `glaka`. Create the filter.

Test it by emailing `you+glaka@gmail.com`, ideally from a different account so
the conversation looks like a real customer's. The message should arrive with
the `glaka` label.

## 2. A Google Cloud project with the Gmail API

1. Go to <https://console.cloud.google.com/> and create a project (e.g. `glaka-dev`).
2. **APIs & Services → Library** → search **Gmail API** → **Enable**.

## 3. OAuth consent (Google Auth Platform)

1. **Google Auth Platform → Branding**: app name `Glaka (dev)`, your email as
   support and developer contact. Save.
2. **Audience**: user type **External**, publishing status **Testing**.
   Under **Test users**, add the Gmail address you are connecting.

While the app is in Testing, Google expires the sign-in after 7 days. When
Glaka says the token is invalid, repeat step 5.

## 4. A desktop OAuth client

1. **Google Auth Platform → Clients → Create client**, type **Desktop app**,
   name `glaka-local`.
2. **Download JSON** and save it in the project as
   `.secrets/gmail-oauth-client.json` (`.secrets/` is gitignored).

## 5. Sign in once

```
uv run python -m glaka.gmail_mcp.server authorize
```

A browser opens. Choose your account. Google warns that the app isn't verified,
which is expected for your own test app: **Continue**, then allow both
permissions. The token is saved to `.secrets/gmail-token.json`.

## 6. Use it

- In the UI (`/ui/<business>`), click **Check email now**, or
- from the terminal: `uv run python -m glaka.cli poll-email --business <slug> --watch 60`

Each new labelled email becomes an enquiry with a draft. Approving it sends the
reply from your Gmail in the same thread.

## Privacy note

Messages that reach Glaka are sent to the configured AI model. With a free
OpenRouter model (`OPENROUTER_ALLOW_DATA_COLLECTION=true`) the host may train on
them, so only route test emails to the label while using one.
