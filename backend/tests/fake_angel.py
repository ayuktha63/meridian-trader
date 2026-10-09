"""An in-memory Angel One: an order book and positions that behave enough
like the real thing to drive the live-order supervisor end to end."""
from __future__ import annotations

import itertools
from datetime import datetime
from typing import Callable, Dict, List, Optional

from app.broker.angelone import angel_broker
from app.broker.angelone.client import AngelOneOrderError
from app.broker.angelone.orders import IST


def broker_now() -> str:
    return datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S")


class FakeAngel:
    def __init__(self) -> None:
        self.orders: List[dict] = []
        self.placed: List[dict] = []
        self.modified: List[dict] = []
        self.cancelled: List[str] = []
        self.ltp: Dict[str, float] = {}
        self.positions_override: Optional[List[dict]] = None
        #: (predicate(params) -> bool, exception) consumed in order.
        self.failures: List[tuple] = []
        self._ids = itertools.count(1001)

    def install(self, monkeypatch) -> "FakeAngel":
        for name in ("place_order", "modify_order", "cancel_order", "get_orders",
                     "get_positions", "get_funds", "get_order_status"):
            monkeypatch.setattr(angel_broker, name, getattr(self, name))
        return self

    # ── scripting ─────────────────────────────────────────────

    def fail_next(self, when: Callable[[dict], bool], exc: Exception,
                  still_places: bool = False) -> None:
        self.failures.append((when, exc, still_places))

    def order(self, order_id: str) -> dict:
        return next(o for o in self.orders if o["orderid"] == order_id)

    def by_type(self, ordertype: str) -> List[dict]:
        return [o for o in self.orders if o["ordertype"] == ordertype]

    def fill(self, order_id: str, quantity: Optional[int] = None,
             price: Optional[float] = None, done: bool = True) -> None:
        order = self.order(order_id)
        total = int(order["quantity"])
        filled = total if quantity is None else quantity
        order["filledshares"] = str(filled)
        order["averageprice"] = str(price if price is not None else
                                    float(order["price"]) or float(order.get("triggerprice") or 0))
        order["status"] = "complete" if (done and filled == total) else "open"
        order["updatetime"] = broker_now()

    def reject(self, order_id: str, text: str = "RMS: rejected") -> None:
        self.order(order_id).update(status="rejected", text=text)

    # ── the broker surface ────────────────────────────────────

    async def place_order(self, params: dict) -> str:
        self.placed.append(dict(params))
        for index, (when, exc, still_places) in enumerate(self.failures):
            if when(params):
                del self.failures[index]
                if still_places:
                    self._book(params)
                raise exc
        return self._book(params)

    def _book(self, params: dict) -> str:
        order_id = "A{}".format(next(self._ids))
        market = params["ordertype"] == "MARKET"
        order = dict(params, orderid=order_id, filledshares="0", averageprice="0",
                     status="trigger pending" if params["variety"] == "STOPLOSS" else "open",
                     updatetime=broker_now(), text="")
        self.orders.append(order)
        if market:
            self.fill(order_id, price=self.ltp.get(params["symboltoken"], 0.0))
        return order_id

    async def modify_order(self, params: dict) -> dict:
        self.modified.append(dict(params))
        order = self.order(params["orderid"])
        if order["status"] in ("complete", "cancelled", "rejected"):
            raise AngelOneOrderError("order is not modifiable")
        order.update({k: v for k, v in params.items() if k in ("triggerprice", "quantity", "price")})
        return {"orderid": params["orderid"]}

    async def cancel_order(self, order_id: str, variety: str = "NORMAL") -> dict:
        order = self.order(order_id)
        if order["status"] in ("complete", "cancelled", "rejected"):
            raise AngelOneOrderError("order is not cancellable")
        order["status"] = "cancelled"
        self.cancelled.append(order_id)
        return {"orderid": order_id}

    async def get_orders(self) -> List[dict]:
        return [dict(o) for o in self.orders]

    async def get_order_status(self, order_id: str) -> Optional[dict]:
        return next((dict(o) for o in self.orders if o["orderid"] == order_id), None)

    async def get_positions(self) -> List[dict]:
        if self.positions_override is not None:
            return self.positions_override
        net: Dict[tuple, int] = {}
        for o in self.orders:
            sign = 1 if o["transactiontype"] == "BUY" else -1
            key = (o["exchange"], o["symboltoken"])
            net[key] = net.get(key, 0) + sign * int(float(o["filledshares"]))
        return [{"exchange": ex, "symboltoken": tok, "netqty": str(qty),
                 "ltp": str(self.ltp.get(tok, 0.0))} for (ex, tok), qty in net.items()]

    async def get_funds(self) -> dict:
        return {"availablecash": "1000000"}
