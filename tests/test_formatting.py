from olx_hunter_bot.agent.hunter import EvaluatedListing
from olx_hunter_bot.bot.formatting import TG_LIMIT, deal_card, pack, split_long, uah


def test_uah():
    assert uah(12500) == "12 500 ₴"
    assert uah(None) == "?"


def test_deal_card_escapes_and_discount():
    card = deal_card(EvaluatedListing(
        listing_id="1", title="<Sony> & co", url="https://olx.ua/x?a=1&b=2",
        price_uah=6000, new_price_uah=10000, location="Київ", score=8, verdict="Добра ціна",
    ))
    assert "&lt;Sony&gt; &amp; co" in card
    assert 'href="https://olx.ua/x?a=1&amp;b=2"' in card
    assert "−40%" in card
    assert "Київ" in card


def test_split_and_pack_respect_limit():
    para = "x" * 1000
    text = "\n\n".join([para] * 10)
    parts = split_long(text)
    assert all(len(p) <= TG_LIMIT for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")

    messages = pack(["a" * 3000, "b" * 3000, "c" * 10])
    assert len(messages) == 2
    assert all(len(m) <= TG_LIMIT for m in messages)
