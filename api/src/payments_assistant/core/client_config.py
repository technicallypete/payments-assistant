"""Ready-to-paste MCP client configuration for an owner API key.

Shown exactly once (CLI `keys create`, `POST /api-keys`, the seed). MCP clients reach the API
through the Next app's `/mcp` rewrite, so the URL is the public app origin, not the API's address.
"""

import json
import os
from dataclasses import dataclass

SERVER_NAME = "penny"


def default_mcp_url() -> str:
    return os.environ.get("APP_ORIGIN", "http://localhost:3010").rstrip("/") + "/mcp"


@dataclass(frozen=True)
class ClientSnippets:
    mcp_url: str
    claude_code: str  # shell command
    claude_desktop: str  # JSON for claude_desktop_config.json (mcp-remote bridge)


def client_snippets(key: str, mcp_url: str | None = None) -> ClientSnippets:
    url = mcp_url or default_mcp_url()
    header = f"Authorization: Bearer {key}"
    claude_code = f'claude mcp add --transport http {SERVER_NAME} {url} --header "{header}"'
    # Claude Desktop's config file launches local commands; `mcp-remote` bridges to a remote
    # streamable-HTTP server and forwards the auth header.
    desktop = {
        "mcpServers": {
            SERVER_NAME: {
                "command": "npx",
                "args": ["-y", "mcp-remote", url, "--header", header],
            }
        }
    }
    return ClientSnippets(
        mcp_url=url, claude_code=claude_code, claude_desktop=json.dumps(desktop, indent=2)
    )


def format_snippets(s: ClientSnippets) -> str:
    return (
        f"MCP endpoint: {s.mcp_url}\n\n"
        f"Claude Code:\n  {s.claude_code}\n\n"
        f"Claude Desktop (claude_desktop_config.json):\n{s.claude_desktop}\n"
    )
