"""Conversational agent. One Claude session per Telegram chat, resumed across messages."""

import asyncio
import logging
from collections import defaultdict

from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, ResultMessage, TextBlock

from olx_hunter_bot.agent.hunt_tools import HUNT_TOOLS, HUNTS_SERVER, build_hunt_tools
from olx_hunter_bot.agent.options import OLX_SERVER, OLX_TOOLS, WEB_TOOLS, base_options, olx_mcp_server
from olx_hunter_bot.agent.prompts import chat_system_prompt
from olx_hunter_bot.config import Settings
from olx_hunter_bot.services import hunts
from olx_hunter_bot.services.runner import HuntRunner

log = logging.getLogger(__name__)


class ChatAgentError(RuntimeError):
    pass


class ChatAgent:
    def __init__(self, settings: Settings, runner: HuntRunner) -> None:
        self._settings = settings
        self._runner = runner
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def ask(self, chat_id: int, text: str) -> str:
        # Messages from one chat are processed one at a time, in order.
        async with self._locks[chat_id]:
            session_id = await hunts.get_agent_session(chat_id)
            try:
                reply, new_session = await self._turn(chat_id, text, session_id)
            except Exception:
                if session_id is None:
                    raise
                # Stale/broken session (e.g. CLI data wiped) → start fresh once.
                log.warning("resume of session %s failed; starting a new one", session_id, exc_info=True)
                reply, new_session = await self._turn(chat_id, text, None)
            await hunts.set_agent_session(chat_id, new_session)
            return reply

    async def reset(self, chat_id: int) -> None:
        async with self._locks[chat_id]:
            await hunts.set_agent_session(chat_id, None)

    async def _turn(self, chat_id: int, text: str, session_id: str | None) -> tuple[str, str]:
        s = self._settings
        options = base_options(
            s,
            system_prompt=chat_system_prompt(
                s.olx_domain, s.hunt_interval_minutes, s.quiet_hours_start, s.quiet_hours_end, s.timezone
            ),
            mcp_servers={
                OLX_SERVER: olx_mcp_server(s),
                HUNTS_SERVER: build_hunt_tools(chat_id, self._runner, s.hunt_interval_minutes),
            },
            allowed_tools=OLX_TOOLS + WEB_TOOLS + HUNT_TOOLS,
            max_turns=s.chat_max_turns,
            resume=session_id,
        )

        texts: list[str] = []
        result: ResultMessage | None = None
        async with ClaudeSDKClient(options) as client:
            await client.query(text)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    texts.extend(b.text for b in msg.content if isinstance(b, TextBlock))
                elif isinstance(msg, ResultMessage):
                    result = msg

        if result is None:
            raise ChatAgentError("agent finished without a result")
        if result.is_error and result.subtype != "error_max_turns":
            raise ChatAgentError(f"agent error ({result.subtype}): {'; '.join(result.errors or [])}")

        reply = (result.result or "").strip() or (texts[-1].strip() if texts else "")
        if result.subtype == "error_max_turns":
            reply = (reply + "\n\n" if reply else "") + "<i>(зупинився: забагато кроків — уточни запит)</i>"
        log.info("chat %s: %d turns, $%s", chat_id, result.num_turns, result.total_cost_usd)
        return reply or "🤷", result.session_id
