import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from olx_hunter_bot.agent.chat import ChatAgent
from olx_hunter_bot.bot.formatting import esc, hunt_line
from olx_hunter_bot.bot.notifier import send_html
from olx_hunter_bot.config import Settings
from olx_hunter_bot.services import hunts
from olx_hunter_bot.services.runner import HuntRunner

log = logging.getLogger(__name__)

def help_text(settings: Settings) -> str:
    return f"""\
👋 <b>OLX Hunter</b> — шукаю вигідні пропозиції на OLX.

Просто напиши, що шукаєш, наприклад:
<i>«Шукаю навушники з шумозаглушенням до 4000 грн»</i>
— я створю пошук, одразу перевірю OLX і надішлю вдалі оголошення.

Хочеш, щоб я перевіряв регулярно? Скажи <i>«стеж за цим»</i> або
/auto <code>N on</code> — тоді шукатиму кожні {settings.hunt_interval_minutes // 60} год
(крім {settings.quiet_hours_start:02d}:00–{settings.quiet_hours_end:02d}:00).

Можна й просто питати: <i>«чи це хороша ціна за MacBook Air M1 за 18к?»</i>

Команди:
/hunts — активні пошуки
/run <code>N</code> — запустити пошук №N зараз
/auto <code>N on|off</code> — автопошук для №N
/close <code>N</code> — закрити пошук №N
/new — почати розмову з чистого аркуша"""


def build_router(settings: Settings, chat_agent: ChatAgent, runner: HuntRunner) -> Router:
    router = Router(name="main")
    router.message.filter(F.from_user.id == settings.owner_tg_user_id)  # personal bot
    help_message = help_text(settings)

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def start(message: Message) -> None:
        await message.answer(help_message)

    @router.message(Command("hunts"))
    async def list_cmd(message: Message) -> None:
        items = await hunts.list_hunts(message.chat.id)
        if not items:
            await message.answer("Активних пошуків немає. Напиши, що шукаєш 🙂")
            return
        lines = [hunt_line(h, runner.is_running(h.id)) for h in items]
        await message.answer("🔎 <b>Активні пошуки</b>\n\n" + "\n\n".join(lines))

    @router.message(Command("run"))
    async def run_cmd(message: Message, command: CommandObject) -> None:
        hunt = await _hunt_from_arg(message, command)
        if hunt is None:
            return
        if runner.trigger(hunt.id, "manual"):
            await message.answer(f"🔍 Шукаю за <b>#{hunt.id} {esc(hunt.title)}</b>… Надішлю результат.")
        else:
            await message.answer(f"⏳ Пошук #{hunt.id} уже виконується.")

    @router.message(Command("auto"))
    async def auto_cmd(message: Message, command: CommandObject) -> None:
        parts = (command.args or "").split()
        if len(parts) != 2 or not parts[0].lstrip("#").isdigit() or parts[1].lower() not in ("on", "off"):
            await message.answer("Формат: /auto <code>N on</code> або /auto <code>N off</code> (див. /hunts)")
            return
        hunt_id, enabled = int(parts[0].lstrip("#")), parts[1].lower() == "on"
        hunt = await hunts.set_scheduled(hunt_id, message.chat.id, enabled)
        if hunt is None:
            await message.answer(f"Активного пошуку #{hunt_id} немає. Див. /hunts")
        elif enabled:
            await message.answer(
                f"🔁 Автопошук для <b>#{hunt.id} {esc(hunt.title)}</b> увімкнено: "
                f"кожні {hunt.interval_minutes // 60} год, крім "
                f"{settings.quiet_hours_start:02d}:00–{settings.quiet_hours_end:02d}:00."
            )
        else:
            await message.answer(f"⏸ Автопошук для <b>#{hunt.id} {esc(hunt.title)}</b> вимкнено. /run {hunt.id} — вручну.")

    @router.message(Command("close"))
    async def close_cmd(message: Message, command: CommandObject) -> None:
        hunt = await _hunt_from_arg(message, command)
        if hunt is None:
            return
        await hunts.close_hunt(hunt.id, message.chat.id)
        await message.answer(f"✅ Пошук <b>#{hunt.id} {esc(hunt.title)}</b> закрито.")

    @router.message(Command("new"))
    async def new_cmd(message: Message) -> None:
        await chat_agent.reset(message.chat.id)
        await message.answer("🧹 Нова розмова. Пошуки залишились без змін.")

    @router.message(F.text)
    async def chat(message: Message, bot: Bot) -> None:
        async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
            try:
                reply = await chat_agent.ask(message.chat.id, message.text or "")
            except Exception as e:
                log.exception("chat agent failed")
                await message.answer(f"⚠️ Помилка агента: <code>{esc(str(e)[:300])}</code>")
                return
        await send_html(bot, message.chat.id, reply)

    @router.message()
    async def other(message: Message) -> None:
        await message.answer("Поки що розумію лише текст ✍️")

    return router


async def _hunt_from_arg(message: Message, command: CommandObject):  # -> Hunt | None
    arg = (command.args or "").strip().lstrip("#")
    if not arg.isdigit():
        await message.answer(f"Вкажи номер пошуку: /{command.command} <code>N</code> (див. /hunts)")
        return None
    hunt = await hunts.get_hunt(int(arg), message.chat.id)
    if hunt is None or hunt.status != "active":
        await message.answer(f"Активного пошуку #{arg} немає. Див. /hunts")
        return None
    return hunt
