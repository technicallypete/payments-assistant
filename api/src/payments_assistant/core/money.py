"""Money formatting. The LLM gets pre-formatted strings so it never does arithmetic on cents."""

_SYMBOLS = {"usd": "$", "eur": "€", "gbp": "£", "cad": "CA$", "aud": "A$"}


def format_money(amount_minor: int, currency: str) -> str:
    cur = currency.lower()
    sign = "-" if amount_minor < 0 else ""
    value = f"{abs(amount_minor) / 100:,.2f}"
    symbol = _SYMBOLS.get(cur)
    return f"{sign}{symbol}{value}" if symbol else f"{sign}{value} {cur.upper()}"
