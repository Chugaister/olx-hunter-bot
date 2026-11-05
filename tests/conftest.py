from pathlib import Path

import pytest

from olx_hunter_bot.agent.hunter import EvaluatedListing
from olx_hunter_bot.config import Settings
from olx_hunter_bot.db.models import Hunt
from olx_hunter_bot.db.session import close_db, init_db


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    fake_mcp = tmp_path / "index.js"
    fake_mcp.write_text("")
    return Settings(
        _env_file=None,
        telegram_bot_token="123:abc",
        owner_tg_user_id=1,
        webhook_base_url="https://example.ngrok.app/",
        webhook_secret="s3cret",
        claude_code_oauth_token="tok",
        olx_mcp_path=fake_mcp,
        data_dir=tmp_path / "data",
        min_deal_score=6,
    )


@pytest.fixture
async def db(settings: Settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await init_db(settings.database_url)
    yield
    await close_db()


class FakeNotifier:
    def __init__(self) -> None:
        self.deals: list[tuple[int, list[EvaluatedListing], str]] = []
        self.texts: list[tuple[int, str]] = []

    async def send_deals(self, hunt: Hunt, deals: list[EvaluatedListing], summary: str) -> None:
        self.deals.append((hunt.id, deals, summary))

    async def send_text(self, chat_id: int, html: str) -> None:
        self.texts.append((chat_id, html))


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()
