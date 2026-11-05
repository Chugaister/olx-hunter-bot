"""Hunt persistence / business rules. All DB access for hunts goes through here."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from olx_hunter_bot.db.models import Chat, Hunt, HuntRun, HuntStatus, RunStatus, SeenListing, utcnow
from olx_hunter_bot.db.session import session


async def create_hunt(
    chat_id: int,
    title: str,
    criteria: dict[str, Any],
    interval_minutes: int,
    scheduled: bool = False,
) -> Hunt:
    async with session() as s:
        hunt = Hunt(
            chat_id=chat_id,
            title=title.strip()[:200],
            criteria=criteria,
            interval_minutes=interval_minutes,
            scheduled=scheduled,
            status=HuntStatus.ACTIVE,
            # The caller runs the first lookup right away; automatic ones follow
            # an interval later, if the hunt is scheduled.
            next_run_at=utcnow() + timedelta(minutes=interval_minutes),
        )
        s.add(hunt)
        await s.commit()
        return hunt


async def list_hunts(chat_id: int, include_closed: bool = False) -> list[Hunt]:
    stmt = select(Hunt).where(Hunt.chat_id == chat_id).order_by(Hunt.id)
    if not include_closed:
        stmt = stmt.where(Hunt.status == HuntStatus.ACTIVE)
    async with session() as s:
        return list((await s.scalars(stmt)).all())


async def get_hunt(hunt_id: int, chat_id: int | None = None) -> Hunt | None:
    async with session() as s:
        hunt = await s.get(Hunt, hunt_id)
    if hunt is None or (chat_id is not None and hunt.chat_id != chat_id):
        return None
    return hunt


async def update_hunt(
    hunt_id: int,
    chat_id: int,
    *,
    title: str | None = None,
    criteria: dict[str, Any] | None = None,
    interval_minutes: int | None = None,
) -> Hunt | None:
    async with session() as s:
        hunt = await s.get(Hunt, hunt_id)
        if hunt is None or hunt.chat_id != chat_id or hunt.status != HuntStatus.ACTIVE:
            return None
        if title is not None:
            hunt.title = title.strip()[:200]
        if criteria is not None:
            hunt.criteria = criteria
        if interval_minutes is not None:
            hunt.interval_minutes = interval_minutes
            hunt.next_run_at = min(hunt.next_run_at, utcnow() + timedelta(minutes=interval_minutes))
        await s.commit()
        return hunt


async def set_scheduled(hunt_id: int, chat_id: int, enabled: bool) -> Hunt | None:
    """Switch a hunt's automatic checks on or off. Switching on starts the clock:
    the next automatic run is one interval from now (use a manual run to check
    immediately)."""
    async with session() as s:
        hunt = await s.get(Hunt, hunt_id)
        if hunt is None or hunt.chat_id != chat_id or hunt.status != HuntStatus.ACTIVE:
            return None
        if enabled and not hunt.scheduled:
            hunt.next_run_at = utcnow() + timedelta(minutes=hunt.interval_minutes)
        hunt.scheduled = enabled
        await s.commit()
        return hunt


async def close_hunt(hunt_id: int, chat_id: int) -> Hunt | None:
    async with session() as s:
        hunt = await s.get(Hunt, hunt_id)
        if hunt is None or hunt.chat_id != chat_id or hunt.status != HuntStatus.ACTIVE:
            return None
        hunt.status = HuntStatus.CLOSED
        hunt.closed_at = utcnow()
        await s.commit()
        return hunt


async def due_hunt_ids(now: datetime) -> list[int]:
    stmt = select(Hunt.id).where(
        Hunt.status == HuntStatus.ACTIVE, Hunt.scheduled.is_(True), Hunt.next_run_at <= now
    )
    async with session() as s:
        return list((await s.scalars(stmt)).all())


async def seen_listing_ids(hunt_id: int, limit: int = 300) -> list[str]:
    stmt = (
        select(SeenListing.listing_id)
        .where(SeenListing.hunt_id == hunt_id)
        .order_by(SeenListing.first_seen_at.desc())
        .limit(limit)
    )
    async with session() as s:
        return list((await s.scalars(stmt)).all())


async def recent_deals(hunt_id: int, limit: int = 10) -> list[SeenListing]:
    stmt = (
        select(SeenListing)
        .where(SeenListing.hunt_id == hunt_id, SeenListing.notified.is_(True))
        .order_by(SeenListing.first_seen_at.desc())
        .limit(limit)
    )
    async with session() as s:
        return list((await s.scalars(stmt)).all())


# ---- runs ---------------------------------------------------------------


async def start_run(hunt_id: int, trigger: str) -> int:
    """Record a run and push next_run_at forward so the scheduler won't re-pick it."""
    async with session() as s:
        hunt = await s.get(Hunt, hunt_id)
        assert hunt is not None
        hunt.next_run_at = utcnow() + timedelta(minutes=hunt.interval_minutes)
        run = HuntRun(hunt_id=hunt_id, trigger=trigger)
        s.add(run)
        await s.commit()
        return run.id


async def finish_run(
    run_id: int,
    *,
    status: RunStatus,
    evaluated: int = 0,
    notified: int = 0,
    cost_usd: float | None = None,
    error: str | None = None,
) -> None:
    async with session() as s:
        run = await s.get(HuntRun, run_id)
        assert run is not None
        run.status = status
        run.finished_at = utcnow()
        run.evaluated_count = evaluated
        run.notified_count = notified
        run.cost_usd = cost_usd
        run.error = error
        hunt = await s.get(Hunt, run.hunt_id)
        if hunt is not None:
            hunt.last_run_at = run.finished_at
        await s.commit()


async def consecutive_failures(hunt_id: int) -> int:
    stmt = (
        select(HuntRun.status)
        .where(HuntRun.hunt_id == hunt_id, HuntRun.status != RunStatus.RUNNING)
        .order_by(HuntRun.id.desc())
        .limit(10)
    )
    async with session() as s:
        statuses = (await s.scalars(stmt)).all()
    count = 0
    for st in statuses:
        if st != RunStatus.ERROR:
            break
        count += 1
    return count


async def mark_runs_interrupted() -> None:
    """Runs left 'running' by a previous process (crash / restart)."""
    async with session() as s:
        runs = (await s.scalars(select(HuntRun).where(HuntRun.status == RunStatus.RUNNING))).all()
        for run in runs:
            run.status = RunStatus.ERROR
            run.finished_at = utcnow()
            run.error = "interrupted (process restarted)"
        await s.commit()


async def store_seen(hunt_id: int, listings: Iterable[SeenListing]) -> list[SeenListing]:
    """Insert listings not seen before; returns only the newly inserted ones."""
    async with session() as s:
        existing = set(
            (
                await s.scalars(
                    select(SeenListing.listing_id).where(SeenListing.hunt_id == hunt_id)
                )
            ).all()
        )
        fresh: list[SeenListing] = []
        for item in listings:
            if item.listing_id in existing:
                continue
            existing.add(item.listing_id)
            item.hunt_id = hunt_id
            s.add(item)
            fresh.append(item)
        await s.commit()
        return fresh


# ---- chat sessions ------------------------------------------------------


async def get_agent_session(chat_id: int) -> str | None:
    async with session() as s:
        chat = await s.get(Chat, chat_id)
        return chat.agent_session_id if chat else None


async def set_agent_session(chat_id: int, session_id: str | None) -> None:
    async with session() as s:
        chat = await s.get(Chat, chat_id)
        if chat is None:
            chat = Chat(chat_id=chat_id)
            s.add(chat)
        chat.agent_session_id = session_id
        await s.commit()
