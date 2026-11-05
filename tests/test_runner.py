import asyncio

import pytest

from olx_hunter_bot.agent.hunter import EvaluatedListing, HuntAgentError, HuntOutput, HuntResult
from olx_hunter_bot.db.models import RunStatus, utcnow
from olx_hunter_bot.services import hunts, runner as runner_mod
from olx_hunter_bot.services.runner import HuntRunner

CHAT = 42


def listing(lid: str, score: int) -> EvaluatedListing:
    return EvaluatedListing(
        listing_id=lid, title=f"Item {lid}", url=f"https://olx.ua/{lid}",
        price_uah=1000, new_price_uah=2000, score=score, verdict="ok",
    )


@pytest.fixture
def agent_calls(monkeypatch):
    """Replace the real agent; tests push outputs (or exceptions) into the queue."""
    queue: list[HuntOutput | Exception] = []
    calls: list[list[str]] = []

    async def fake_agent(hunt, seen_ids, settings):
        calls.append(list(seen_ids))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return HuntResult(output=item, cost_usd=0.01, num_turns=3)

    monkeypatch.setattr(runner_mod, "run_hunt_agent", fake_agent)
    return queue, calls


async def run_and_wait(runner: HuntRunner, hunt_id: int, trigger="scheduled") -> None:
    assert runner.trigger(hunt_id, trigger)
    while runner.is_running(hunt_id):
        await asyncio.sleep(0.01)


async def test_dedup_and_score_threshold(db, settings, notifier, agent_calls):
    queue, calls = agent_calls
    hunt = await hunts.create_hunt(CHAT, "Headphones", {"query": "sony"}, 180)
    runner = HuntRunner(settings, notifier)

    queue.append(HuntOutput(summary="s1", listings=[listing("a", 9), listing("b", 3), listing("c", 6)]))
    await run_and_wait(runner, hunt.id)

    assert len(notifier.deals) == 1
    _, deals, summary = notifier.deals[0]
    assert [d.listing_id for d in deals] == ["a", "c"]  # sorted by score, b below threshold
    assert summary == "s1"

    # Second run: agent repeats "a" — it must not be sent again, and seen ids are passed in.
    queue.append(HuntOutput(summary="s2", listings=[listing("a", 9), listing("d", 7)]))
    await run_and_wait(runner, hunt.id)
    assert set(calls[1]) == {"a", "b", "c"}
    assert [d.listing_id for d in notifier.deals[1][1]] == ["d"]

    reloaded = await hunts.get_hunt(hunt.id)
    assert reloaded.next_run_at > utcnow()
    assert reloaded.last_run_at is not None


async def test_manual_run_with_nothing_new_reports_back(db, settings, notifier, agent_calls):
    queue, _ = agent_calls
    hunt = await hunts.create_hunt(CHAT, "Laptop", {"query": "thinkpad"}, 180)
    runner = HuntRunner(settings, notifier)

    queue.append(HuntOutput(summary="quiet market", listings=[]))
    await run_and_wait(runner, hunt.id, "scheduled")
    assert notifier.texts == []  # scheduled + nothing → silence

    queue.append(HuntOutput(summary="quiet market", listings=[]))
    await run_and_wait(runner, hunt.id, "manual")
    assert len(notifier.texts) == 1 and "quiet market" in notifier.texts[0][1]


async def test_closed_hunt_is_not_run(db, settings, notifier, agent_calls):
    _, calls = agent_calls
    hunt = await hunts.create_hunt(CHAT, "X", {"query": "x"}, 180)
    await hunts.close_hunt(hunt.id, CHAT)
    await run_and_wait(HuntRunner(settings, notifier), hunt.id)
    assert calls == []
    assert await hunts.due_hunt_ids(utcnow()) == []


async def test_failures_alert_on_third_consecutive(db, settings, notifier, agent_calls):
    queue, _ = agent_calls
    hunt = await hunts.create_hunt(CHAT, "X", {"query": "x"}, 180)
    runner = HuntRunner(settings, notifier)

    for _ in range(4):
        queue.append(HuntAgentError("olx down"))
        await run_and_wait(runner, hunt.id)
    assert len(notifier.texts) == 1  # only on the 3rd failure, not the 4th
    assert "3 рази" in notifier.texts[0][1]
    assert await hunts.consecutive_failures(hunt.id) == 4


async def test_trigger_is_not_reentrant(db, settings, notifier, agent_calls):
    queue, calls = agent_calls
    hunt = await hunts.create_hunt(CHAT, "X", {"query": "x"}, 180)
    runner = HuntRunner(settings, notifier)
    queue.append(HuntOutput(summary="", listings=[]))
    assert runner.trigger(hunt.id, "manual")
    assert not runner.trigger(hunt.id, "scheduled")
    while runner.is_running(hunt.id):
        await asyncio.sleep(0.01)
    assert len(calls) == 1


async def test_interrupted_runs_marked_on_startup(db):
    hunt = await hunts.create_hunt(CHAT, "X", {"query": "x"}, 180)
    await hunts.start_run(hunt.id, "scheduled")
    await hunts.mark_runs_interrupted()
    assert await hunts.consecutive_failures(hunt.id) == 1
    assert RunStatus.ERROR == "error"
