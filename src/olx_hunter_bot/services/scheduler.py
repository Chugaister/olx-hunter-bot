"""Periodic lookups: every tick, start the scheduled hunts whose next_run_at has passed,
except during quiet hours."""

import asyncio
import logging
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from olx_hunter_bot.config import Settings
from olx_hunter_bot.services import hunts
from olx_hunter_bot.services.runner import HuntRunner

log = logging.getLogger(__name__)


def in_quiet_hours(local: time, start_hour: int, end_hour: int) -> bool:
    """True if `local` falls in [start_hour, end_hour), which may wrap past midnight
    (e.g. 23 → 6). start == end means no quiet hours."""
    if start_hour == end_hour:
        return False
    start, end = time(start_hour), time(end_hour)
    if start < end:
        return start <= local < end
    return local >= start or local < end


async def scheduler_tick(runner: HuntRunner, settings: Settings, now: datetime | None = None) -> int:
    """One scheduling pass. Returns how many hunts were started.

    During quiet hours nothing is started; hunts that come due stay due and all
    start when quiet hours end. Manual runs bypass this entirely.
    """
    now = now or datetime.now(UTC)
    local = now.astimezone(ZoneInfo(settings.timezone)).time()
    if in_quiet_hours(local, settings.quiet_hours_start, settings.quiet_hours_end):
        return 0
    started = 0
    for hunt_id in await hunts.due_hunt_ids(now.astimezone(UTC).replace(tzinfo=None)):
        if runner.trigger(hunt_id, "scheduled"):  # no-op if already running
            started += 1
    return started


async def scheduler_loop(runner: HuntRunner, settings: Settings) -> None:
    log.info(
        "scheduler started (tick %ss, quiet %02d:00-%02d:00 %s)",
        settings.scheduler_tick_seconds,
        settings.quiet_hours_start,
        settings.quiet_hours_end,
        settings.timezone,
    )
    while True:
        try:
            await scheduler_tick(runner, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(settings.scheduler_tick_seconds)
