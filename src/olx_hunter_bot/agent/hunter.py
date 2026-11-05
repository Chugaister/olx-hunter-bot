"""One-shot hunt agent: searches OLX for one hunt and returns scored listings."""

import json
import logging
from dataclasses import dataclass
from datetime import date

from claude_agent_sdk import ClaudeSDKClient, ResultMessage
from pydantic import BaseModel, Field, ValidationError

from olx_hunter_bot.agent.options import OLX_SERVER, OLX_TOOLS, WEB_TOOLS, base_options, olx_mcp_server
from olx_hunter_bot.agent.prompts import HUNT_OUTPUT_SCHEMA, HUNT_SYSTEM_PROMPT, hunt_prompt
from olx_hunter_bot.config import Settings
from olx_hunter_bot.db.models import Hunt

log = logging.getLogger(__name__)


class HuntAgentError(RuntimeError):
    pass


class EvaluatedListing(BaseModel):
    listing_id: str = Field(min_length=1, max_length=64)
    title: str
    url: str
    price_uah: float | None = None
    new_price_uah: float | None = None
    location: str | None = None
    score: int = Field(ge=0, le=10)
    verdict: str


class HuntOutput(BaseModel):
    summary: str
    listings: list[EvaluatedListing]


@dataclass
class HuntResult:
    output: HuntOutput
    cost_usd: float | None
    num_turns: int


async def run_hunt_agent(hunt: Hunt, seen_ids: list[str], settings: Settings) -> HuntResult:
    options = base_options(
        settings,
        system_prompt=HUNT_SYSTEM_PROMPT.format(domain=settings.olx_domain, today=date.today()),
        mcp_servers={OLX_SERVER: olx_mcp_server(settings)},
        allowed_tools=OLX_TOOLS + WEB_TOOLS,
        max_turns=settings.hunt_max_turns,
        output_format={"type": "json_schema", "schema": HUNT_OUTPUT_SCHEMA},
    )
    prompt = hunt_prompt(hunt.title, hunt.criteria, settings.olx_domain, seen_ids)

    result: ResultMessage | None = None
    async with ClaudeSDKClient(options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, ResultMessage):
                result = msg

    if result is None:
        raise HuntAgentError("agent finished without a result")
    if result.is_error:
        detail = "; ".join(result.errors or []) or result.result or result.subtype
        raise HuntAgentError(f"agent error ({result.subtype}): {detail}")

    raw = result.structured_output
    if raw is None and result.result:
        try:
            raw = json.loads(result.result)
        except json.JSONDecodeError as e:
            raise HuntAgentError("agent returned no structured output") from e
    try:
        output = HuntOutput.model_validate(raw)
    except ValidationError as e:
        raise HuntAgentError(f"invalid agent output: {e}") from e

    log.info(
        "hunt %s: %d listings evaluated, %d turns, $%s",
        hunt.id, len(output.listings), result.num_turns, result.total_cost_usd,
    )
    return HuntResult(output=output, cost_usd=result.total_cost_usd, num_turns=result.num_turns)
