import asyncio
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, Update
from fastapi import FastAPI, Header, HTTPException, Request, Response

from olx_hunter_bot.agent.chat import ChatAgent
from olx_hunter_bot.bot.handlers import build_router
from olx_hunter_bot.bot.notifier import TelegramNotifier
from olx_hunter_bot.config import get_settings
from olx_hunter_bot.db.session import close_db, init_db
from olx_hunter_bot.services import background, hunts
from olx_hunter_bot.services.runner import HuntRunner
from olx_hunter_bot.services.scheduler import scheduler_loop

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="hunts", description="Активні пошуки"),
    BotCommand(command="run", description="Запустити пошук зараз: /run N"),
    BotCommand(command="close", description="Закрити пошук: /close N"),
    BotCommand(command="auto", description="Автопошук: /auto N on|off"),
    BotCommand(command="new", description="Нова розмова"),
    BotCommand(command="help", description="Довідка"),
]


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        await init_db(settings.database_url)
        await hunts.mark_runs_interrupted()

        bot = Bot(
            settings.telegram_bot_token,
            default=DefaultBotProperties(
                parse_mode=ParseMode.HTML,
                link_preview_is_disabled=True,
            ),
        )
        runner = HuntRunner(settings, TelegramNotifier(bot))
        chat_agent = ChatAgent(settings, runner)
        dp = Dispatcher()
        dp.include_router(build_router(settings, chat_agent, runner))

        await bot.set_my_commands(COMMANDS)
        await bot.set_webhook(
            settings.webhook_base_url + settings.webhook_path,
            secret_token=settings.webhook_secret,
            allowed_updates=["message"],
        )
        log.info("webhook set to %s%s", settings.webhook_base_url, settings.webhook_path)

        app.state.bot, app.state.dp = bot, dp
        scheduler = asyncio.create_task(
            scheduler_loop(runner, settings), name="scheduler"
        )
        try:
            yield
        finally:
            scheduler.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler
            await background.cancel_all()
            await bot.session.close()
            await close_db()

    app = FastAPI(title="OLX Hunter Bot", lifespan=lifespan)

    @app.post(settings.webhook_path)
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> Response:
        if not hmac.compare_digest(x_telegram_bot_api_secret_token or "", settings.webhook_secret):
            raise HTTPException(status_code=403)
        bot: Bot = request.app.state.bot
        dp: Dispatcher = request.app.state.dp
        update = Update.model_validate(await request.json(), context={"bot": bot})
        # Agent turns take minutes; Telegram retries webhooks that don't answer quickly.
        background.spawn(dp.feed_update(bot, update), name=f"update-{update.update_id}")
        return Response(status_code=200)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
