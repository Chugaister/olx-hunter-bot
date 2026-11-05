"""Executes hunts: agent call → dedup → persist → notify."""

import asyncio
import logging
from html import escape
from typing import Literal, Protocol

from olx_hunter_bot.agent.hunter import EvaluatedListing, HuntOutput, run_hunt_agent
from olx_hunter_bot.config import Settings
from olx_hunter_bot.db.models import Hunt, HuntStatus, RunStatus, SeenListing
from olx_hunter_bot.services import hunts
from olx_hunter_bot.services.background import spawn

log = logging.getLogger(__name__)

Trigger = Literal["scheduled", "manual"]
FAILURE_ALERT_THRESHOLD = 3


class Notifier(Protocol):
    async def send_deals(
        self, hunt: Hunt, deals: list[EvaluatedListing], summary: str
    ) -> None: ...

    async def send_text(self, chat_id: int, html: str) -> None: ...


class HuntRunner:
    def __init__(self, settings: Settings, notifier: Notifier) -> None:
        self._settings = settings
        self._notifier = notifier
        self._sem = asyncio.Semaphore(settings.max_concurrent_hunts)
        self._running: set[int] = set()

    def is_running(self, hunt_id: int) -> bool:
        return hunt_id in self._running

    def trigger(self, hunt_id: int, trigger: Trigger) -> bool:
        """Start a run in the background. False if this hunt is already running."""
        if hunt_id in self._running:
            return False
        # Reserve synchronously so two triggers in the same tick can't both start.
        self._running.add(hunt_id)
        spawn(self._run(hunt_id, trigger), name=f"hunt-{hunt_id}-{trigger}")
        return True

    async def _run(self, hunt_id: int, trigger: Trigger) -> None:
        try:
            async with self._sem:
                await self._run_locked(hunt_id, trigger)
        finally:
            self._running.discard(hunt_id)

    async def _run_locked(self, hunt_id: int, trigger: Trigger) -> None:
        hunt = await hunts.get_hunt(hunt_id)
        if hunt is None or hunt.status != HuntStatus.ACTIVE:
            return
        run_id = await hunts.start_run(hunt_id, trigger)
        log.info("hunt %s (%s) started: %s", hunt_id, trigger, hunt.title)

        try:
            seen = await hunts.seen_listing_ids(hunt_id)
            result = await run_hunt_agent(hunt, seen, self._settings)
        except asyncio.CancelledError:
            await hunts.finish_run(run_id, status=RunStatus.ERROR, error="cancelled")
            raise
        except Exception as e:
            log.exception("hunt %s failed", hunt_id)
            await hunts.finish_run(run_id, status=RunStatus.ERROR, error=f"{type(e).__name__}: {e}")
            failures = await hunts.consecutive_failures(hunt_id)
            if trigger == "manual" or failures == FAILURE_ALERT_THRESHOLD:
                await self._safe_text(
                    hunt.chat_id,
                    f"⚠️ Пошук <b>#{hunt.id} {_esc(hunt.title)}</b> не вдався"
                    + (f" ({failures} рази поспіль)" if failures > 1 else "")
                    + f".\n<code>{_esc(str(e)[:300])}</code>",
                )
            return

        deals = await self._persist(hunt_id, result.output)

        # The user may have closed the hunt while the agent was working.
        current = await hunts.get_hunt(hunt_id)
        if current is not None and current.status == HuntStatus.ACTIVE:
            try:
                if deals:
                    await self._notifier.send_deals(current, deals, result.output.summary)
                elif trigger == "manual":
                    await self._notifier.send_text(
                        hunt.chat_id,
                        f"🔍 <b>#{hunt.id} {_esc(hunt.title)}</b>: нових вигідних пропозицій немає.\n"
                        f"<i>{_esc(result.output.summary)}</i>",
                    )
            except Exception:
                log.exception("failed to notify about hunt %s", hunt_id)

        await hunts.finish_run(
            run_id,
            status=RunStatus.OK,
            evaluated=len(result.output.listings),
            notified=len(deals),
            cost_usd=result.cost_usd,
        )

    async def _persist(self, hunt_id: int, output: HuntOutput) -> list[EvaluatedListing]:
        """Store everything evaluated; return the fresh ones that are good enough to send."""
        min_score = self._settings.min_deal_score
        by_id = {item.listing_id: item for item in output.listings}
        rows = [
            SeenListing(
                listing_id=item.listing_id,
                title=item.title[:300],
                url=item.url[:500],
                price_uah=item.price_uah,
                score=item.score,
                notified=item.score >= min_score,
            )
            for item in by_id.values()
        ]
        fresh = await hunts.store_seen(hunt_id, rows)
        deals = [by_id[row.listing_id] for row in fresh if row.notified]
        return sorted(deals, key=lambda d: d.score, reverse=True)

    async def _safe_text(self, chat_id: int, html: str) -> None:
        try:
            await self._notifier.send_text(chat_id, html)
        except Exception:
            log.exception("failed to send message to %s", chat_id)


def _esc(s: str) -> str:
    return escape(s, quote=False)
