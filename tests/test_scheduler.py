from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from olx_hunter_bot.config import Settings
from olx_hunter_bot.db.models import Hunt, utcnow
from olx_hunter_bot.db.session import close_db, init_db, session
from olx_hunter_bot.services import hunts
from olx_hunter_bot.services.scheduler import in_quiet_hours, scheduler_tick

KYIV = ZoneInfo("Europe/Kyiv")
CHAT = 42


class FakeRunner:
    def __init__(self) -> None:
        self.triggered: list[tuple[int, str]] = []

    def trigger(self, hunt_id: int, trigger: str) -> bool:
        self.triggered.append((hunt_id, trigger))
        return True


def kyiv(hour: int, minute: int = 0) -> datetime:
    """A moment at the given Kyiv wall-clock time, as an aware UTC datetime."""
    return datetime(2026, 11, 5, hour, minute, tzinfo=KYIV).astimezone(UTC)


@pytest.mark.parametrize(
    ("hhmm", "quiet"),
    [
        ((0, 59), False),
        ((1, 0), True),  # start is inclusive
        ((3, 30), True),
        ((6, 59), True),
        ((7, 0), False),  # end is exclusive
        ((23, 0), False),
    ],
)
def test_quiet_hours_window(hhmm, quiet):
    assert in_quiet_hours(time(*hhmm), 1, 7) is quiet


def test_quiet_hours_can_wrap_midnight():
    assert in_quiet_hours(time(23, 30), 23, 6)
    assert in_quiet_hours(time(2, 0), 23, 6)
    assert not in_quiet_hours(time(12, 0), 23, 6)


def test_equal_start_and_end_disables_quiet_hours():
    assert not in_quiet_hours(time(3, 0), 5, 5)


async def make_due(hunt_id: int) -> None:
    """Pretend the hunt's next automatic run is long overdue (before any test clock)."""
    async with session() as s:
        hunt = await s.get(Hunt, hunt_id)
        hunt.next_run_at = datetime(2000, 1, 1)
        await s.commit()


async def test_new_hunts_are_not_scheduled(db, settings):
    hunt = await hunts.create_hunt(CHAT, "x", {"query": "x"}, 180)
    assert hunt.scheduled is False
    await make_due(hunt.id)

    runner = FakeRunner()
    assert await scheduler_tick(runner, settings, now=kyiv(12)) == 0
    assert runner.triggered == []


async def test_scheduled_due_hunt_runs_outside_quiet_hours(db, settings):
    hunt = await hunts.create_hunt(CHAT, "x", {"query": "x"}, 180, scheduled=True)
    await make_due(hunt.id)

    runner = FakeRunner()
    assert await scheduler_tick(runner, settings, now=kyiv(12)) == 1
    assert runner.triggered == [(hunt.id, "scheduled")]


async def test_nothing_runs_during_quiet_hours_and_due_hunts_wait_until_they_end(db, settings):
    hunt = await hunts.create_hunt(CHAT, "x", {"query": "x"}, 180, scheduled=True)
    await make_due(hunt.id)
    runner = FakeRunner()

    for hour in (1, 3, 6):
        assert await scheduler_tick(runner, settings, now=kyiv(hour, 30)) == 0
    assert runner.triggered == []

    # Still due at 07:00, so it runs then.
    assert await scheduler_tick(runner, settings, now=kyiv(7, 0)) == 1
    assert runner.triggered == [(hunt.id, "scheduled")]


async def test_set_scheduled_toggles_and_starts_the_clock(db):
    hunt = await hunts.create_hunt(CHAT, "x", {"query": "x"}, 180)
    await make_due(hunt.id)

    on = await hunts.set_scheduled(hunt.id, CHAT, True)
    assert on.scheduled is True
    # Switching on doesn't fire immediately: the first automatic run is an interval away.
    assert on.next_run_at > utcnow() + timedelta(minutes=170)
    assert hunt.id not in await hunts.due_hunt_ids(utcnow())

    off = await hunts.set_scheduled(hunt.id, CHAT, False)
    assert off.scheduled is False
    assert await hunts.set_scheduled(hunt.id, 999, True) is None  # another chat's hunt


async def test_existing_database_gets_the_new_column(tmp_path, settings: Settings):
    """A DB created before `scheduled` existed is upgraded on startup; old hunts stay unscheduled."""
    import aiosqlite

    db_path = settings.data_dir / "olx_hunter.db"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "CREATE TABLE hunts (id INTEGER PRIMARY KEY, chat_id BIGINT, title VARCHAR(200), "
            "criteria JSON, interval_minutes INTEGER, status VARCHAR(16), next_run_at DATETIME, "
            "last_run_at DATETIME, created_at DATETIME, closed_at DATETIME)"
        )
        await conn.execute(
            "INSERT INTO hunts (chat_id, title, criteria, interval_minutes, status, next_run_at, created_at) "
            "VALUES (42, 'old', '{}', 180, 'active', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        await conn.commit()

    await init_db(settings.database_url)
    try:
        (old,) = await hunts.list_hunts(42)
        assert old.title == "old" and old.scheduled is False
        assert await hunts.due_hunt_ids(utcnow()) == []
    finally:
        await close_db()
