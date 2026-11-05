import asyncio

from sqlalchemy import text

from olx_hunter_bot.db.session import session
from olx_hunter_bot.services import hunts


async def pragma(name: str):
    async with session() as s:
        return (await s.execute(text(f"PRAGMA {name}"))).scalar()


async def test_sqlite_runs_in_wal_mode(db, settings):
    assert await pragma("journal_mode") == "wal"
    assert await pragma("synchronous") == 1  # NORMAL
    assert await pragma("busy_timeout") == 5000
    # WAL keeps its log next to the database file.
    await hunts.create_hunt(1, "x", {"query": "x"}, 180)
    assert (settings.data_dir / "olx_hunter.db-wal").exists()


async def test_concurrent_writes_do_not_fail(db):
    """Hunt runs and chat turns write from separate tasks at the same time."""
    hunt = await hunts.create_hunt(1, "x", {"query": "x"}, 180)

    async def run_once(i: int) -> None:
        run_id = await hunts.start_run(hunt.id, "scheduled")
        await hunts.set_agent_session(1000 + i, f"session-{i}")
        await hunts.finish_run(run_id, status="ok", evaluated=i)

    await asyncio.gather(*(run_once(i) for i in range(20)))
    assert await hunts.consecutive_failures(hunt.id) == 0
