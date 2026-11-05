from pathlib import Path

import pytest
from pydantic import ValidationError

from olx_hunter_bot.agent.options import olx_mcp_server
from olx_hunter_bot.config import Settings

BASE = dict(
    _env_file=None,
    telegram_bot_token="123:abc",
    owner_tg_user_id=1,
    webhook_base_url="https://example.ngrok.app",
    webhook_secret="s",
    claude_code_oauth_token="t",
)


def test_http_url_used_without_local_build(tmp_path: Path):
    s = Settings(**BASE, olx_mcp_url="http://127.0.0.1:3000/mcp", olx_mcp_path=tmp_path / "missing.js")
    assert olx_mcp_server(s) == {"type": "http", "url": "http://127.0.0.1:3000/mcp"}


def test_stdio_fallback(settings: Settings):
    cfg = olx_mcp_server(settings)
    assert cfg["type"] == "stdio" and cfg["args"] == [str(settings.olx_mcp_path)]


def test_missing_both_fails(tmp_path: Path):
    with pytest.raises(ValidationError, match="OLX_MCP_URL"):
        Settings(**BASE, olx_mcp_path=tmp_path / "missing.js")


def test_bad_url_fails(tmp_path: Path):
    with pytest.raises(ValidationError, match="http"):
        Settings(**BASE, olx_mcp_url="127.0.0.1:3000/mcp")
