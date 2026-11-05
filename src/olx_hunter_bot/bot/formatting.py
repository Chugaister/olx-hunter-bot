from datetime import datetime
from html import escape

from olx_hunter_bot.agent.hunter import EvaluatedListing
from olx_hunter_bot.db.models import Hunt, utcnow

TG_LIMIT = 4096


def esc(s: str) -> str:
    return escape(s, quote=False)


def uah(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:,.0f}".replace(",", " ") + " ₴"


def deal_card(d: EvaluatedListing) -> str:
    lines = [f"<b>{d.score}/10</b> · <a href=\"{escape(d.url)}\">{esc(d.title)}</a>"]
    price = f"💰 {uah(d.price_uah)}"
    if d.new_price_uah:
        price += f"  (нове ≈ {uah(d.new_price_uah)}"
        if d.price_uah:
            price += f", −{round((1 - d.price_uah / d.new_price_uah) * 100)}%"
        price += ")"
    lines.append(price)
    if d.location:
        lines.append(f"📍 {esc(d.location)}")
    lines.append(f"<i>{esc(d.verdict)}</i>")
    return "\n".join(lines)


def deals_message(hunt: Hunt, deals: list[EvaluatedListing], summary: str) -> list[str]:
    header = f"🎯 <b>#{hunt.id} {esc(hunt.title)}</b> — нових пропозицій: {len(deals)}"
    footer = f"<i>{esc(summary)}</i>" if summary else ""
    return pack([header, *(deal_card(d) for d in deals), footer])


def hunt_line(hunt: Hunt, running: bool) -> str:
    c = hunt.criteria
    budget = ""
    if c.get("min_price") or c.get("max_price"):
        budget = f" · {uah(c.get('min_price')) if c.get('min_price') else '0'}–{uah(c.get('max_price'))}"
    if running:
        state = "⏳ шукаю зараз"
    elif hunt.scheduled:
        state = f"🔁 кожні {hunt.interval_minutes // 60} год · наступний: {_ago(hunt.next_run_at)}"
    else:
        state = f"⏸ автопошук вимкнено (/auto {hunt.id} on)"
    return f"<b>#{hunt.id}</b> {esc(hunt.title)}{budget}\n    {state}"


def _ago(dt: datetime) -> str:
    minutes = round((dt - utcnow()).total_seconds() / 60)
    if minutes <= 0:
        return "зараз"
    if minutes < 60:
        return f"за {minutes} хв"
    return f"за {minutes // 60} год {minutes % 60} хв"


def pack(blocks: list[str], sep: str = "\n\n") -> list[str]:
    """Join blocks into as few messages as possible under Telegram's length limit."""
    messages: list[str] = []
    current = ""
    for block in filter(None, blocks):
        for piece in split_long(block):
            candidate = f"{current}{sep}{piece}" if current else piece
            if len(candidate) > TG_LIMIT:
                messages.append(current)
                current = piece
            else:
                current = candidate
    if current:
        messages.append(current)
    return messages


def split_long(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Split text at paragraph/line boundaries so each part fits in one message."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut <= 0:
            cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts
