import logging
import re

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from olx_hunter_bot.agent.hunter import EvaluatedListing
from olx_hunter_bot.bot.formatting import deals_message, pack
from olx_hunter_bot.db.models import Hunt

log = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")


async def send_html(bot: Bot, chat_id: int, html: str) -> None:
    """Send (possibly long, possibly malformed) HTML; falls back to plain text per part."""
    for part in pack([html]):
        try:
            await bot.send_message(chat_id, part)
        except TelegramBadRequest as e:
            if "parse" not in str(e).lower() and "tag" not in str(e).lower():
                raise
            log.warning("HTML rejected by Telegram, sending as plain text: %s", e)
            plain = _TAG.sub("", part).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
            await bot.send_message(chat_id, plain, parse_mode=None)


class TelegramNotifier:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_deals(self, hunt: Hunt, deals: list[EvaluatedListing], summary: str) -> None:
        for message in deals_message(hunt, deals, summary):
            await send_html(self._bot, hunt.chat_id, message)

    async def send_text(self, chat_id: int, html: str) -> None:
        await send_html(self._bot, chat_id, html)
