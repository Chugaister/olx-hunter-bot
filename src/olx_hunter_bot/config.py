from functools import lru_cache
from pathlib import Path

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Telegram
    telegram_bot_token: str
    owner_tg_user_id: int  # personal bot: only this user may talk to it
    webhook_base_url: str  # public HTTPS URL, e.g. the ngrok forwarding URL
    webhook_secret: str  # random string; Telegram echoes it in a header

    # Claude
    claude_code_oauth_token: str
    agent_model: str | None = None  # None → Claude Code default
    hunt_max_turns: int = 40
    chat_max_turns: int = 30

    # Hunting
    olx_domain: str = "olx.ua"
    hunt_interval_minutes: int = 180  # used once a hunt's automatic checks are switched on
    min_deal_score: int = 6  # 0-10; listings scored below this are not sent
    scheduler_tick_seconds: int = 60
    max_concurrent_hunts: int = 2
    # No automatic runs in [start, end) local time; due hunts wait until `end`.
    # Manual runs (/run, "check now") are not affected. start == end disables it.
    quiet_hours_start: int = Field(default=1, ge=0, le=23)
    quiet_hours_end: int = Field(default=7, ge=0, le=23)
    timezone: str = "Europe/Kyiv"

    # Infra
    # olx-mcp over Streamable HTTP (e.g. the docker-compose service): http://127.0.0.1:3000/mcp
    olx_mcp_url: str | None = None
    # Fallback when OLX_MCP_URL is unset: spawn a local build over stdio for each agent session
    olx_mcp_path: Path = PROJECT_ROOT.parent / "olx-mcp" / "dist" / "index.js"
    data_dir: Path = PROJECT_ROOT / "data"
    host: str = "127.0.0.1"
    port: int = 8000

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        self.webhook_base_url = self.webhook_base_url.rstrip("/")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"Unknown TIMEZONE {self.timezone!r}") from e
        if not self.webhook_base_url.startswith("https://"):
            raise ValueError("WEBHOOK_BASE_URL must be an https:// URL (Telegram requirement)")
        if self.olx_mcp_url:
            if not self.olx_mcp_url.startswith(("http://", "https://")):
                raise ValueError("OLX_MCP_URL must be an http(s):// URL")
        elif not self.olx_mcp_path.is_file():
            raise ValueError(
                f"olx-mcp not found: set OLX_MCP_URL (docker compose up -d olx-mcp) "
                f"or build it locally so {self.olx_mcp_path} exists"
            )
        return self

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir / 'olx_hunter.db'}"

    @property
    def agent_workdir(self) -> Path:
        return self.data_dir / "agent-workdir"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def webhook_path(self) -> str:
        return "/tg/webhook"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
