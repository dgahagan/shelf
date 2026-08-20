"""Currency registry and display formatting.

Display formatting only — Shelf never converts between currencies. A user
picks a display currency in Settings and every value surface (stats, item
detail, valuation report, valuation run log) renders amounts through
`format_money` using that currency's symbol placement, grouping, and
decimal precision. No amount is ever multiplied or divided by an exchange
rate here.

Sibling of `app/nav.py`: same settings-backed, request-time-cached shape,
including the swallow-and-default error path in `get_currency()` — a broken
settings read must not take down every page that renders a price.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Currency:
    code: str               # ISO 4217, e.g. "EUR"
    name: str                # "Euro" — for the settings dropdown
    symbol: str               # "€"
    suffix: bool = False       # symbol renders after the number ("120 kr")
    decimals: int = 2           # 0 for JPY/KRW/HUF


CURRENCIES: dict[str, Currency] = {
    c.code: c
    for c in (
        Currency("USD", "US Dollar", "$"),
        Currency("EUR", "Euro", "€"),
        Currency("GBP", "British Pound", "£"),
        Currency("CAD", "Canadian Dollar", "CA$"),
        Currency("AUD", "Australian Dollar", "A$"),
        Currency("NZD", "New Zealand Dollar", "NZ$"),
        Currency("CHF", "Swiss Franc", "CHF"),
        Currency("JPY", "Japanese Yen", "¥", decimals=0),
        Currency("CNY", "Chinese Yuan", "¥"),
        Currency("KRW", "South Korean Won", "₩", decimals=0),
        Currency("INR", "Indian Rupee", "₹"),
        Currency("SEK", "Swedish Krona", "kr", suffix=True),
        Currency("NOK", "Norwegian Krone", "kr", suffix=True),
        Currency("DKK", "Danish Krone", "kr", suffix=True),
        Currency("PLN", "Polish Złoty", "zł", suffix=True),
        Currency("CZK", "Czech Koruna", "Kč", suffix=True),
        Currency("HUF", "Hungarian Forint", "Ft", suffix=True, decimals=0),
        Currency("BRL", "Brazilian Real", "R$"),
        Currency("MXN", "Mexican Peso", "$"),
        Currency("ZAR", "South African Rand", "R"),
    )
}

_cached_currency: Currency | None = None


def invalidate_cache() -> None:
    """Drop the cached currency so the next render re-reads the setting."""
    global _cached_currency
    _cached_currency = None


def get_currency() -> Currency:
    """The configured display currency; an unknown or absent code falls back
    to USD.

    There is deliberately no env-var path for this setting (see G15): with
    no env override, `get_setting` and `get_all_settings` agree, so the
    settings page can read the dropdown value straight from its existing
    `get_all_settings` dict.

    Follows `nav._nav_settings()` (`app/nav.py:47-74`) including the
    swallow-and-default error path: a broken settings read logs a warning
    and returns the USD default for this render, never raises, and tries
    again on the next one.
    """
    global _cached_currency
    if _cached_currency is None:
        from app.database import get_db, get_setting
        try:
            with get_db() as db:
                code = get_setting(db, "currency")
        except Exception:
            logger.warning("Could not read currency setting; falling back to USD", exc_info=True)
            return CURRENCIES["USD"]
        _cached_currency = CURRENCIES.get((code or "").strip().upper(), CURRENCIES["USD"])
    return _cached_currency


def format_money(value, decimals: int | None = None) -> str:
    """Render `value` in the configured display currency.

    Prefix currencies render tight (`€1,234.56`), suffix currencies render
    with a space before the symbol (`1,234.56 kr`). Thousands separators
    always use US-style grouping. `decimals=None` uses the currency's own
    precision (0 for JPY/KRW/HUF); an explicit `decimals=` overrides it.
    """
    currency = get_currency()
    dec = currency.decimals if decimals is None else decimals
    amount = float(value)
    sign = "-" if amount < 0 else ""
    formatted = f"{abs(amount):,.{dec}f}"
    if currency.suffix:
        return f"{sign}{formatted} {currency.symbol}"
    return f"{sign}{currency.symbol}{formatted}"
