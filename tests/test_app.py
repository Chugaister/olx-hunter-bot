"""Starts the real FastAPI app (lifespan included) with Telegram's network calls stubbed."""

from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from fastapi.testclient import TestClient

from olx_hunter_bot import app as app_module
from olx_hunter_bot.config import Settings


@pytest.fixture
def client(settings: Settings, monkeypatch):
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    set_webhook = AsyncMock(return_value=True)
    monkeypatch.setattr(Bot, "set_webhook", set_webhook)
    monkeypatch.setattr(Bot, "set_my_commands", AsyncMock(return_value=True))
    feed_update = AsyncMock()
    monkeypatch.setattr("aiogram.Dispatcher.feed_update", feed_update)

    with TestClient(app_module.create_app()) as test_client:
        test_client.set_webhook = set_webhook  # type: ignore[attr-defined]
        test_client.feed_update = feed_update  # type: ignore[attr-defined]
        yield test_client


def test_startup_registers_webhook(client, settings):
    client.set_webhook.assert_awaited_once()
    args, kwargs = client.set_webhook.call_args
    assert args[0] == "https://example.ngrok.app/tg/webhook"
    assert kwargs["secret_token"] == settings.webhook_secret
    assert client.get("/health").json() == {"status": "ok"}


UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 1,
        "date": 0,
        "chat": {"id": 1, "type": "private"},
        "from": {"id": 1, "is_bot": False, "first_name": "Owner"},
        "text": "/hunts",
    },
}


def test_webhook_rejects_wrong_secret(client):
    response = client.post("/tg/webhook", json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
    assert response.status_code == 403
    client.feed_update.assert_not_called()


def test_webhook_accepts_update_with_secret(client, settings):
    response = client.post(
        "/tg/webhook", json=UPDATE, headers={"X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret}
    )
    assert response.status_code == 200
    client.feed_update.assert_called_once()
