"""What Angel One itself says is open: positions and orders, however they were
placed — by this application, or by hand in the broker's own app.

One read serves every screen. The position and order books share the API's
rate limit with everything else, so an answer is kept for a few seconds and
a failed read is reported as unreadable, never as an empty book.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from ..cache import cache
from ..instruments import INSTRUMENTS
from ..schemas.execution import BrokerBook, BrokerOrder, BrokerPosition
from .angelone import angel_auth, angel_broker

CACHE_KEY = "angel_one:book"
CACHE_TTL_SECONDS = 4

#: Order states that are finished. Anything else is still working at the broker.
FINISHED = ("complete", "completed", "rejected", "cancelled", "canceled")


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def meridian_symbol(row: Dict[str, Any]) -> Optional[str]:
    """The symbol this application charts a broker row under, or None when it
    does not list that instrument."""
    name = str(row.get("symbolname") or "").upper()
    for symbol, instrument in INSTRUMENTS.items():
        if (instrument.broker_symbol or symbol).upper() == name:
            return symbol
    return None


def _position(row: Dict[str, Any], managed: set) -> Optional[BrokerPosition]:
    quantity = _number(row.get("netqty"))
    if quantity == 0:
        return None
    trading_symbol = str(row.get("tradingsymbol") or "")
    return BrokerPosition(
        symbol=str(row.get("symbolname") or trading_symbol), chart_symbol=meridian_symbol(row),
        trading_symbol=trading_symbol, exchange=str(row.get("exchange") or ""),
        product=str(row.get("producttype") or ""), net_quantity=quantity,
        buy_average=_number(row.get("buyavgprice")), sell_average=_number(row.get("sellavgprice")),
        last_price=_number(row.get("ltp")) or None, pnl=_number(row.get("pnl")),
        realised=_number(row.get("realised")), unrealised=_number(row.get("unrealised")),
        buy_quantity=_number(row.get("buyqty")), sell_quantity=_number(row.get("sellqty")),
        managed=trading_symbol in managed)


def _order(row: Dict[str, Any]) -> BrokerOrder:
    status = str(row.get("orderstatus") or row.get("status") or "").lower()
    return BrokerOrder(
        order_id=str(row.get("orderid") or ""),
        symbol=str(row.get("tradingsymbol") or ""), chart_symbol=meridian_symbol(
            {"symbolname": str(row.get("tradingsymbol") or "").split("-")[0]}),
        exchange=str(row.get("exchange") or ""), side=str(row.get("transactiontype") or ""),
        order_type=str(row.get("ordertype") or ""), product=str(row.get("producttype") or ""),
        quantity=_number(row.get("quantity")), filled=_number(row.get("filledshares")),
        price=_number(row.get("price")) or None,
        trigger_price=_number(row.get("triggerprice")) or None,
        average_price=_number(row.get("averageprice")) or None,
        status=status, working=status not in FINISHED, message=str(row.get("text") or ""),
        updated_at=str(row.get("updatetime") or ""))


async def read(managed: Optional[set] = None) -> Optional[BrokerBook]:
    """The broker's books now, or None when they could not be read."""
    if not angel_auth.connected:
        return None
    managed = managed or set()
    cached = await cache.get_json(CACHE_KEY)
    if cached is None:
        try:
            positions, orders = await asyncio.gather(
                angel_broker.get_positions(), angel_broker.get_orders())
        except Exception:  # noqa: BLE001 - a refused read must not break the screen
            return None
        try:
            conditional = await angel_broker.get_conditional_orders()
        except Exception:  # noqa: BLE001 - optional: the rest of the book still stands
            conditional = None
        cached = {"positions": positions or [], "orders": orders or [],
                  "conditional": conditional, "as_of": time.time()}
        await cache.set_json(CACHE_KEY, cached, ttl=CACHE_TTL_SECONDS)
    held: List[BrokerPosition] = [p for p in (_position(r, managed) for r in cached["positions"]) if p]
    orders = [_order(r) for r in cached["orders"]]
    return BrokerBook(positions=held, orders=orders, conditional=cached.get("conditional"),
                      realised_today=sum(_number(r.get("realised")) for r in cached["positions"]),
                      as_of=cached["as_of"])
