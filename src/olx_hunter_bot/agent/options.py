"""Shared Claude Agent SDK configuration."""

import logging
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import McpHttpServerConfig, McpStdioServerConfig

from olx_hunter_bot.config import Settings

log = logging.getLogger("olx_hunter_bot.agent.cli")

OLX_SERVER = "olx"
OLX_TOOLS = [f"mcp__{OLX_SERVER}__searchListings", f"mcp__{OLX_SERVER}__getListingDetails"]
WEB_TOOLS = ["WebSearch", "WebFetch"]


def olx_mcp_server(settings: Settings) -> McpHttpServerConfig | McpStdioServerConfig:
    if settings.olx_mcp_url:
        return {"type": "http", "url": settings.olx_mcp_url}
    return {"type": "stdio", "command": "node", "args": [str(settings.olx_mcp_path)]}


def base_options(settings: Settings, **overrides: Any) -> ClaudeAgentOptions:
    """Locked-down options: only web tools as built-ins, no filesystem settings,
    nothing runs unless it is listed in allowed_tools."""
    settings.agent_workdir.mkdir(parents=True, exist_ok=True)
    return ClaudeAgentOptions(
        tools=list(WEB_TOOLS),  # no Bash/Read/Edit/... at all
        permission_mode="dontAsk",  # deny anything not in allowed_tools
        setting_sources=[],  # ignore ~/.claude and project settings
        strict_mcp_config=True,  # only the MCP servers we pass
        cwd=settings.agent_workdir,
        model=settings.agent_model,
        env={"CLAUDE_CODE_OAUTH_TOKEN": settings.claude_code_oauth_token},
        stderr=lambda line: log.debug(line.rstrip()),
        **overrides,
    )
