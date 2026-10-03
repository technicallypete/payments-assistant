import json

from payments_assistant.core.client_config import client_snippets, default_mcp_url, format_snippets


def test_snippets_carry_url_and_key(monkeypatch):
    monkeypatch.setenv("APP_ORIGIN", "https://pay.example/")
    assert default_mcp_url() == "https://pay.example/mcp"
    s = client_snippets("pak_abc")
    assert s.mcp_url == "https://pay.example/mcp"
    assert s.claude_code == (
        "claude mcp add --transport http penny https://pay.example/mcp "
        '--header "Authorization: Bearer pak_abc"'
    )
    desktop = json.loads(s.claude_desktop)
    args = desktop["mcpServers"]["penny"]["args"]
    assert args == [
        "-y",
        "mcp-remote",
        "https://pay.example/mcp",
        "--header",
        "Authorization: Bearer pak_abc",
    ]


def test_explicit_url_and_formatting():
    s = client_snippets("pak_x", "http://localhost:3010/mcp")
    text = format_snippets(s)
    assert "MCP endpoint: http://localhost:3010/mcp" in text
    assert "claude mcp add" in text and "mcpServers" in text
