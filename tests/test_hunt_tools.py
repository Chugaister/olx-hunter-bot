import json

import pytest

from olx_hunter_bot.agent.hunt_tools import HUNT_TOOL_NAMES, hunt_tools
from olx_hunter_bot.services import hunts

CHAT = 42


class FakeRunner:
    def __init__(self) -> None:
        self.triggered: list[tuple[int, str]] = []

    def trigger(self, hunt_id: int, trigger: str) -> bool:
        self.triggered.append((hunt_id, trigger))
        return True

    def is_running(self, hunt_id: int) -> bool:
        return False


@pytest.fixture
def tools():
    runner = FakeRunner()
    by_name = {t.name: t.handler for t in hunt_tools(CHAT, runner, 180)}  # type: ignore[arg-type]
    return by_name, runner


def text(result) -> str:
    return result["content"][0]["text"]


async def test_tool_names_match(tools):
    assert set(tools[0]) == set(HUNT_TOOL_NAMES)


async def test_create_update_close(db, tools):
    t, runner = tools
    res = await t["create_hunt"]({
        "title": "Навушники", "query": "sony wh-1000xm4", "max_price": 4000,
        "must_have": ["ANC"], "avoid": [], "interval_hours": 6,
    })
    hunt_id = json.loads(text(res))["created"]["id"]
    assert runner.triggered == [(hunt_id, "manual")]

    hunt = await hunts.get_hunt(hunt_id)
    assert hunt.criteria == {"query": "sony wh-1000xm4", "max_price": 4000, "must_have": ["ANC"]}
    assert hunt.interval_minutes == 360

    await t["update_hunt"]({"hunt_id": hunt_id, "max_price": 5000, "must_have": []})
    hunt = await hunts.get_hunt(hunt_id)
    assert hunt.criteria == {"query": "sony wh-1000xm4", "max_price": 5000}

    res = await t["close_hunt"]({"hunt_id": hunt_id})
    assert "closed" in text(res)
    res = await t["run_hunt_now"]({"hunt_id": hunt_id})
    assert res.get("is_error")


async def test_tools_cannot_touch_other_chats(db, tools):
    t, _ = tools
    other = await hunts.create_hunt(999, "Other", {"query": "x"}, 180)
    for name in ("close_hunt", "run_hunt_now", "update_hunt", "recent_deals"):
        res = await t[name]({"hunt_id": other.id})
        assert res.get("is_error"), name
    listed = json.loads(text(await t["list_hunts"]({})))
    assert listed == []


async def test_schedule_is_off_unless_requested(db, tools):
    t, runner = tools
    plain = json.loads(text(await t["create_hunt"]({"title": "a", "query": "a"})))["created"]
    watched = json.loads(text(await t["create_hunt"]({"title": "b", "query": "b", "schedule": True})))["created"]

    assert plain["automatic_checks"] is False and plain["next_run_at_utc"] is None
    assert watched["automatic_checks"] is True
    # Both still get their first lookup right away.
    assert [trigger for _, trigger in runner.triggered] == ["manual", "manual"]


async def test_update_hunt_switches_schedule(db, tools):
    t, _ = tools
    hunt_id = json.loads(text(await t["create_hunt"]({"title": "a", "query": "a"})))["created"]["id"]

    on = json.loads(text(await t["update_hunt"]({"hunt_id": hunt_id, "schedule": True})))["updated"]
    assert on["automatic_checks"] is True
    off = json.loads(text(await t["update_hunt"]({"hunt_id": hunt_id, "schedule": False})))["updated"]
    assert off["automatic_checks"] is False
    # Pausing is not closing.
    assert (await hunts.get_hunt(hunt_id)).status == "active"


async def test_interval_has_floor(db, tools):
    t, _ = tools
    res = await t["create_hunt"]({"title": "x", "query": "x", "interval_hours": 0.01})
    assert json.loads(text(res))["created"]["interval_hours"] == 0.5
