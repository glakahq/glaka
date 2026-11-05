"""Glaka's Gmail MCP server: two tools, minimal scopes, safety rules enforced here.

Run as an MCP server over stdio (Glaka starts it for you):
    uv run python -m glaka.gmail_mcp.server
One-time Google sign-in (opens a browser, saves a token in .secrets/):
    uv run python -m glaka.gmail_mcp.server authorize

Rules this server enforces, whatever the caller asks:
- it only reads messages carrying the configured label (default "glaka");
- it only replies inside a labelled thread, to someone who wrote in that thread;
- it skips Glaka's own replies, so it never answers itself.
"""

import sys
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from glaka.config import get_settings
from glaka.gmail_mcp.gmail import (
    SCOPES,
    GmailApi,
    GoogleGmailApi,
    build_reply,
    is_glaka_reply,
    parse_message,
)
from glaka.gmail_mcp.schemas import Email, SentEmail


def create_server(make_api: Callable[[], GmailApi], label: str) -> MCPServer:
    server = MCPServer(
        "glaka-gmail",
        instructions=f"Read and reply to customer emails labelled '{label}' in Gmail.",
    )

    @server.tool()
    def list_new_emails(max_results: int = 20, newer_than_days: int = 7) -> list[Email]:
        """Emails labelled for Glaka from the last few days, newest first (Glaka's own replies excluded)."""
        api = make_api()
        max_results = max(1, min(max_results, 50))
        newer_than_days = max(1, min(newer_than_days, 30))
        ids = api.search(f"label:{label} newer_than:{newer_than_days}d", max_results)
        messages = [api.get_message(message_id) for message_id in ids]
        return [parse_message(m) for m in messages if not is_glaka_reply(m)]

    @server.tool()
    def send_reply(
        thread_id: str, to: str, subject: str, body: str, in_reply_to: str = "", references: str = ""
    ) -> SentEmail:
        """Reply in an existing labelled thread to someone who wrote in it."""
        api = make_api()
        label_id = api.label_id(label)
        labels, senders = api.thread_info(thread_id)
        if label_id is None or label_id not in labels:
            raise ToolError(f"thread is not labelled '{label}'")
        if to.strip().lower() not in senders:
            raise ToolError("recipient did not write in this thread")
        if not body.strip():
            raise ToolError("reply body is empty")
        try:
            raw = build_reply(to.strip(), subject, body, in_reply_to, references)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        sent = api.send(raw, thread_id)
        return SentEmail(id=sent["id"], thread_id=sent.get("threadId", thread_id))

    return server


def _google_api() -> GmailApi:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    settings = get_settings()
    if not settings.gmail_token_file.exists():
        raise ToolError("Gmail is not authorised yet: run `python -m glaka.gmail_mcp.server authorize`")
    creds = Credentials.from_authorized_user_file(str(settings.gmail_token_file), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            settings.gmail_token_file.write_text(creds.to_json())
        else:
            raise ToolError("Gmail token is invalid: run `python -m glaka.gmail_mcp.server authorize` again")
    return GoogleGmailApi(build("gmail", "v1", credentials=creds, cache_discovery=False))


def authorize() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    settings = get_settings()
    if not settings.gmail_client_file.exists():
        sys.exit(f"Missing {settings.gmail_client_file}. See docs/gmail-setup.md, step 4.")
    flow = InstalledAppFlow.from_client_secrets_file(str(settings.gmail_client_file), SCOPES)
    creds = flow.run_local_server(port=0)
    settings.gmail_token_file.parent.mkdir(parents=True, exist_ok=True)
    settings.gmail_token_file.write_text(creds.to_json())
    settings.gmail_token_file.chmod(0o600)
    print(f"Saved Gmail token to {settings.gmail_token_file}")


if __name__ == "__main__":
    if sys.argv[1:] == ["authorize"]:
        authorize()
    else:
        create_server(_google_api, get_settings().gmail_label).run("stdio")
