"""In-process MCP tools that let the chat agent manage hunts for one chat."""

import json
from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig

from olx_hunter_bot.db.models import Hunt
from olx_hunter_bot.services import hunts
from olx_hunter_bot.services.runner import HuntRunner

HUNTS_SERVER = "hunts"
HUNT_TOOL_NAMES = [
    "create_hunt",
    "list_hunts",
    "update_hunt",
    "close_hunt",
    "run_hunt_now",
    "recent_deals",
]
HUNT_TOOLS = [f"mcp__{HUNTS_SERVER}__{name}" for name in HUNT_TOOL_NAMES]

_CRITERIA_PROPS: dict[str, Any] = {
    "query": {"type": "string", "description": "Search phrase for OLX, e.g. 'Sony WH-1000XM4'."},
    "min_price": {"type": "number", "description": "Minimum price, UAH."},
    "max_price": {"type": "number", "description": "Maximum price, UAH."},
    "location": {"type": "string", "description": "City/region in Ukraine, if the user cares."},
    "must_have": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Hard requirements (e.g. '16GB RAM', 'original box').",
    },
    "avoid": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Deal-breakers (e.g. 'broken', 'replica', 'Lenovo').",
    },
    "notes": {"type": "string", "description": "Anything else useful for judging listings."},
}
_CRITERIA_KEYS = list(_CRITERIA_PROPS)


def _ok(payload: Any) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": text}]}


def _err(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


def _hunt_view(h: Hunt, running: bool = False) -> dict[str, Any]:
    return {
        "id": h.id,
        "title": h.title,
        "status": h.status,
        "criteria": h.criteria,
        "automatic_checks": h.scheduled,
        "interval_hours": round(h.interval_minutes / 60, 2),
        "next_run_at_utc": h.next_run_at if h.scheduled else None,
        "last_run_at_utc": h.last_run_at,
        "running_now": running,
    }


def _interval(args: dict[str, Any]) -> int | None:
    hours = args.get("interval_hours")
    if hours is None:
        return None
    return max(30, round(float(hours) * 60))  # at least every 30 min


def build_hunt_tools(chat_id: int, runner: HuntRunner, default_interval_minutes: int) -> McpSdkServerConfig:
    return create_sdk_mcp_server(
        HUNTS_SERVER, tools=hunt_tools(chat_id, runner, default_interval_minutes)
    )


def hunt_tools(chat_id: int, runner: HuntRunner, default_interval_minutes: int) -> list[SdkMcpTool[Any]]:
    """Tools bound to one chat — the agent can never touch another chat's hunts."""

    @tool(
        "create_hunt",
        "Create a saved search (hunt). It runs once right away; good listings are sent to the "
        "user as a separate message. Automatic re-checks are OFF unless schedule=true.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short human title, e.g. 'Навушники з ANC до 4000₴'."},
                **_CRITERIA_PROPS,
                "schedule": {
                    "type": "boolean",
                    "description": "Re-check automatically every interval_hours. Default false: set true "
                    "only if the user explicitly asks for regular/automatic checking.",
                },
                "interval_hours": {"type": "number", "description": "How often to re-check when scheduled. Default 3."},
            },
            "required": ["title", "query"],
        },
    )
    async def create_hunt(args: dict[str, Any]) -> dict[str, Any]:
        criteria = {k: args[k] for k in _CRITERIA_KEYS if args.get(k) not in (None, "", [])}
        hunt = await hunts.create_hunt(
            chat_id,
            args["title"],
            criteria,
            _interval(args) or default_interval_minutes,
            scheduled=bool(args.get("schedule")),
        )
        runner.trigger(hunt.id, "manual")  # manual → user hears back even if nothing found
        return _ok({"created": _hunt_view(hunt, True), "note": "First lookup started; results arrive as a separate message."})

    @tool(
        "list_hunts",
        "List the user's hunts.",
        {
            "type": "object",
            "properties": {"include_closed": {"type": "boolean", "description": "Also show closed hunts."}},
        },
    )
    async def list_hunts(args: dict[str, Any]) -> dict[str, Any]:
        items = await hunts.list_hunts(chat_id, include_closed=bool(args.get("include_closed")))
        return _ok([_hunt_view(h, runner.is_running(h.id)) for h in items])

    @tool(
        "update_hunt",
        "Change an active hunt. Given criteria fields replace the old values; others are kept. "
        "schedule=true/false switches automatic re-checks on/off.",
        {
            "type": "object",
            "properties": {
                "hunt_id": {"type": "integer"},
                "title": {"type": "string"},
                **_CRITERIA_PROPS,
                "schedule": {"type": "boolean", "description": "Automatic re-checks on (true) or off (false)."},
                "interval_hours": {"type": "number"},
            },
            "required": ["hunt_id"],
        },
    )
    async def update_hunt(args: dict[str, Any]) -> dict[str, Any]:
        hunt = await hunts.get_hunt(int(args["hunt_id"]), chat_id)
        if hunt is None:
            return _err(f"Hunt {args['hunt_id']} not found.")
        criteria = dict(hunt.criteria)
        for k in _CRITERIA_KEYS:
            if k in args:
                if args[k] in (None, "", []):
                    criteria.pop(k, None)
                else:
                    criteria[k] = args[k]
        updated = await hunts.update_hunt(
            hunt.id, chat_id, title=args.get("title"), criteria=criteria, interval_minutes=_interval(args)
        )
        if updated is None:
            return _err(f"Hunt {hunt.id} is closed.")
        if "schedule" in args:
            updated = await hunts.set_scheduled(hunt.id, chat_id, bool(args["schedule"])) or updated
        return _ok({"updated": _hunt_view(updated, runner.is_running(updated.id))})

    @tool(
        "close_hunt",
        "Close a hunt for good (the user found the item or no longer needs it). To only stop "
        "automatic checks, use update_hunt with schedule=false instead.",
        {"type": "object", "properties": {"hunt_id": {"type": "integer"}}, "required": ["hunt_id"]},
    )
    async def close_hunt(args: dict[str, Any]) -> dict[str, Any]:
        hunt = await hunts.close_hunt(int(args["hunt_id"]), chat_id)
        if hunt is None:
            return _err(f"Hunt {args['hunt_id']} not found or already closed.")
        return _ok(f"Hunt #{hunt.id} '{hunt.title}' closed.")

    @tool(
        "run_hunt_now",
        "Trigger a lookup for a hunt immediately. Runs in the background; results arrive "
        "as a separate message, so don't wait for them.",
        {"type": "object", "properties": {"hunt_id": {"type": "integer"}}, "required": ["hunt_id"]},
    )
    async def run_hunt_now(args: dict[str, Any]) -> dict[str, Any]:
        hunt = await hunts.get_hunt(int(args["hunt_id"]), chat_id)
        if hunt is None or hunt.status != "active":
            return _err(f"Active hunt {args['hunt_id']} not found.")
        if not runner.trigger(hunt.id, "manual"):
            return _ok(f"Hunt #{hunt.id} is already running.")
        return _ok(f"Lookup for hunt #{hunt.id} started.")

    @tool(
        "recent_deals",
        "Deals already sent to the user for a hunt (newest first).",
        {
            "type": "object",
            "properties": {"hunt_id": {"type": "integer"}, "limit": {"type": "integer"}},
            "required": ["hunt_id"],
        },
    )
    async def recent_deals(args: dict[str, Any]) -> dict[str, Any]:
        hunt = await hunts.get_hunt(int(args["hunt_id"]), chat_id)
        if hunt is None:
            return _err(f"Hunt {args['hunt_id']} not found.")
        deals = await hunts.recent_deals(hunt.id, limit=int(args.get("limit") or 10))
        return _ok(
            [
                {"listing_id": d.listing_id, "title": d.title, "url": d.url,
                 "price_uah": d.price_uah, "score": d.score, "found_at_utc": d.first_seen_at}
                for d in deals
            ]
        )

    return [create_hunt, list_hunts, update_hunt, close_hunt, run_hunt_now, recent_deals]
