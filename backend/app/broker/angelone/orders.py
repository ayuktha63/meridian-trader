"""Pure helpers for building and matching Angel One orders — no I/O.

Kept apart from the execution service so each rule that once went wrong
(unrounded prices, time-blind order matching) is testable on its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, Optional

IST = timezone(timedelta(hours=5, minutes=30))

#: Angel One accepts at most 20 characters in `ordertag`.
TAG_LENGTH = 20

_TIME_FORMATS = ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M")


def order_tag(recommendation_id: str, leg: str = "") -> str:
    """Idempotency tag carried on every order we place, so an order can be
    found again after a timeout by what it *is* rather than by guessing.

    `leg` distinguishes the entry from its protective orders ("S", "T").
    """
    return (recommendation_id[:TAG_LENGTH - len(leg)] + leg)[:TAG_LENGTH]


def round_to_tick(price: float, tick: float) -> str:
    """`price` on the instrument's tick grid, as the string the API wants.

    Sent unrounded, a price off the grid is rejected by the exchange; worse,
    `str(float)` can yield `2500.0000000000005`.
    """
    if tick <= 0:
        raise ValueError("tick size must be positive")
    step = Decimal(str(tick))
    ticks = (Decimal(str(price)) / step).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    value = ticks * step
    places = max(0, -step.as_tuple().exponent)
    return format(value.quantize(Decimal(1).scaleb(-places)), "f")


def parse_broker_time(text: Optional[str]) -> Optional[float]:
    """Angel One timestamps are IST wall-clock strings, not epoch."""
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=IST).timestamp()
        except ValueError:
            continue
    return None


def find_order_by_tag(orders: Iterable[Dict[str, Any]], tag: str, *,
                      since: float, trading_symbol: str, side: str,
                      quantity: int) -> Optional[Dict[str, Any]]:
    """The order we placed, or None. Never an older, similar one.

    Needs the tag *and* the instrument, side and size to agree, and the
    order to have been touched since `since` (minus a little clock skew). An
    order with no readable timestamp is not trusted.
    """
    for order in orders:
        if order.get("ordertag") != tag:
            continue
        if (order.get("tradingsymbol") != trading_symbol
                or order.get("transactiontype") != side
                or str(order.get("quantity")) != str(int(quantity))):
            continue
        stamp = parse_broker_time(order.get("updatetime") or order.get("exchtime"))
        if stamp is None or stamp < since - 60:
            continue
        return order
    return None
