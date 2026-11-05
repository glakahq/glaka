"""Glaka's side of MCP: a client that talks to the Gmail MCP server.

MailClient is a protocol, so the workflow doesn't care whether mail goes over
MCP to Gmail, to another provider, or to a fake in tests.
"""

import asyncio
import os
import sys
from typing import Any, Protocol

from mcp import Client, StdioServerParameters
from mcp.server.mcpserver import MCPServer
from pydantic import TypeAdapter

from glaka.gmail_mcp.schemas import Email, SentEmail


class MailError(RuntimeError):
    """The mail server failed or refused a request."""


class MailClient(Protocol):
    def fetch_new(self) -> list[Email]: ...
    def send_reply(
        self, thread_id: str, to: str, subject: str, body: str, in_reply_to: str, references: str
    ) -> SentEmail: ...


def gmail_server_process() -> StdioServerParameters:
    """Start our Gmail MCP server as a child process speaking MCP over stdin/stdout."""
    return StdioServerParameters(
        command=sys.executable, args=["-m", "glaka.gmail_mcp.server"], cwd=os.getcwd()
    )


class McpMailClient:
    def __init__(self, server: StdioServerParameters | MCPServer) -> None:
        self.server = server  # a process to launch, or (in tests) an in-process server

    async def _call(self, tool: str, arguments: dict[str, Any]) -> Any:
        async with Client(self.server, read_timeout_seconds=60) as client:
            result = await client.call_tool(tool, arguments)
        if result.is_error:
            text = " ".join(getattr(block, "text", "") for block in result.content)
            raise MailError(f"{tool} failed: {text or 'unknown error'}")
        return (result.structured_content or {}).get("result", result.structured_content)

    def _run(self, tool: str, arguments: dict[str, Any]) -> Any:
        # Our app is synchronous; the MCP SDK is async. Run one short event loop per call.
        try:
            return asyncio.run(self._call(tool, arguments))
        except MailError:
            raise
        except Exception as exc:  # process failed to start, timeout, protocol error...
            raise MailError(f"{tool} failed: {exc}") from exc

    def fetch_new(self) -> list[Email]:
        return TypeAdapter(list[Email]).validate_python(self._run("list_new_emails", {}))

    def send_reply(
        self, thread_id: str, to: str, subject: str, body: str, in_reply_to: str, references: str
    ) -> SentEmail:
        return SentEmail.model_validate(
            self._run(
                "send_reply",
                {
                    "thread_id": thread_id,
                    "to": to,
                    "subject": subject,
                    "body": body,
                    "in_reply_to": in_reply_to,
                    "references": references,
                },
            )
        )
