"""The cost engine checked against the broker's own arithmetic.

    python -m app.data.angel_charges        compare a spread of orders, print every line

`costs_india.py` holds rates read off a price list. Angel One also publishes
a calculator (SmartAPI `estimateCharges`): give it an order, it returns what
that order would be charged, line by line. It places nothing — it is the
same sum the engine does, done by the people who send the bill — so the two
can be compared, and a rate that was misread or has since changed shows up
as a line that does not agree.

What the calculator leaves out: the depository (DP) charge on a delivery
sale. That is levied per stock per day, not per order, and no order's
estimate includes it. The engine's DP charge therefore rests on the price
list alone, and the comparison is of everything else.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import costs_india
from ..costs_india import BUY, DELIVERY, INTRADAY, SELL, Charges

#: The calculator's name for each line, and the engine's.
LINES = {
    "Angel One Brokerage": "brokerage",
    "Exchange Transaction Charges": "exchange",
    "Stamp Duty": "stamp",
    "IPFT charges": "ipft",
    "SEBI Fees": "sebi",
    "Security Transaction Tax": "stt",
    "GST": "gst",
}
PRODUCT_NAMES = {DELIVERY: "DELIVERY", INTRADAY: "INTRADAY"}

#: Two sums of the same order may differ by rounding, not by more.
TOLERANCE_RUPEES = 0.01

#: Order values that land on each branch of the brokerage rule: under the
#: Rs 5 floor, on the percentage, and over the Rs 20 cap.
SPREAD: Tuple[Tuple[int, float], ...] = ((1, 1340.0), (10, 1340.0), (200, 1340.0))


class EstimateUnreadable(RuntimeError):
    pass


def parse(response: Any) -> Dict[str, float]:
    """The calculator's answer for one order as {engine line: rupees}, with
    its own total. Raises if a line it sent is one nobody has mapped: an
    unknown charge must not be dropped from the comparison unnoticed."""
    if not isinstance(response, dict) or not response.get("status"):
        message = response.get("message") if isinstance(response, dict) else type(response).__name__
        raise EstimateUnreadable("The charge estimate failed: {}".format(message))
    try:
        summary = response["data"]["summary"]
        out: Dict[str, float] = {"total": float(summary["total_charges"]),
                                 "turnover": float(summary["trade_value"])}
        pending = list(summary["breakup"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EstimateUnreadable("The charge estimate has no summary.") from exc
    while pending:
        item = pending.pop()
        if item.get("breakup"):
            pending.extend(item["breakup"])          # a group: its lines are what count
            continue
        name = item.get("name")
        if name in LINES:
            out[LINES[name]] = float(item.get("amount") or 0.0)
        elif name not in ("External Charges", "Taxes") and float(item.get("amount") or 0.0):
            raise EstimateUnreadable("The calculator charges {!r}, which the engine does "
                                     "not model.".format(name))
    return out


def without_dp(engine: Charges, gst_rate: float) -> Charges:
    """The engine's charges less the DP charge and the GST on it — what the
    calculator, which includes neither, can be compared with."""
    return replace(engine, dp=0.0, gst=engine.gst - gst_rate * engine.dp)


def differences(engine: Charges, broker: Dict[str, float], gst_rate: float,
                tolerance: float = TOLERANCE_RUPEES) -> List[str]:
    """Each line on which the engine and the calculator disagree, and the
    total. The DP charge is left out of both: the calculator does not
    include it."""
    comparable = without_dp(engine, gst_rate)
    out = []
    for line in LINES.values():
        ours, theirs = getattr(comparable, line), broker.get(line, 0.0)
        if abs(ours - theirs) > tolerance:
            out.append("{}: engine {:.4f}, broker {:.4f}".format(line, ours, theirs))
    if abs(comparable.total - broker.get("total", 0.0)) > tolerance:
        out.append("total excluding DP: engine {:.4f}, broker {:.4f}".format(
            comparable.total, broker.get("total", 0.0)))
    return out


def order_params(product: str, side: str, quantity: int, price: float, *,
                 symbol: str, token: str, exchange: str = "NSE") -> Dict[str, Any]:
    return {"orders": [{
        "product_type": PRODUCT_NAMES[product], "transaction_type": side,
        "quantity": str(int(quantity)), "price": str(price), "exchange": exchange,
        "symbol_name": symbol, "token": str(token)}]}


async def verify(estimate, *, symbol: str = "RELIANCE", token: str = "2885",
                 spread: Sequence[Tuple[int, float]] = SPREAD,
                 pause: float = 1.1, sleep=asyncio.sleep) -> List[Dict[str, Any]]:
    """Every product, side and order size in `spread`, engine against
    calculator. `estimate` takes the calculator's parameters and returns its
    response. One row per order; `differences` is empty where they agree."""
    rows: List[Dict[str, Any]] = []
    for product in (DELIVERY, INTRADAY):
        rates = costs_india.schedule(product)
        for side in (BUY, SELL):
            for quantity, price in spread:
                if rows:
                    await sleep(pause)
                value = quantity * price
                engine = costs_india.order_charges(rates, side, value)
                row: Dict[str, Any] = {"product": product, "side": side, "value": value,
                                       "engine": engine.to_dict(), "schedule": rates.id}
                try:
                    broker = parse(await estimate(order_params(
                        product, side, quantity, price, symbol=symbol, token=token)))
                    row.update(broker=broker,
                               differences=differences(engine, broker, rates.gst))
                except EstimateUnreadable as exc:
                    row.update(broker=None, differences=[str(exc)])
                rows.append(row)
    return rows


async def _live() -> int:
    from ..broker.angelone.auth import angel_auth

    async def estimate(params: Dict[str, Any]) -> Any:
        client = await asyncio.to_thread(angel_auth.ensure)
        return await asyncio.to_thread(client.estimateCharges, params)

    rows = await verify(estimate)
    print("{:<9} {:<4} {:>10} {:>10} {:>10} {:>8}  {}".format(
        "product", "side", "value", "engine", "broker", "dp", "agreement"))
    wrong = 0
    for row in rows:
        broker: Optional[Dict[str, float]] = row["broker"]
        wrong += bool(row["differences"])
        dp = row["engine"]["dp"] * (1 + costs_india.schedule(row["product"]).gst)
        print("{:<9} {:<4} {:>10.0f} {:>10.4f} {:>10} {:>8.2f}  {}".format(
            row["product"], row["side"], row["value"], row["engine"]["total"] - dp,
            "-" if broker is None else "{:.4f}".format(broker["total"]), dp,
            "agree" if not row["differences"] else "; ".join(row["differences"])))
    print("\n{} of {} orders agree line by line (engine total shown without the DP "
          "charge, which the calculator leaves out).".format(len(rows) - wrong, len(rows)))
    print("schedules: {}".format(", ".join(sorted({r["schedule"] for r in rows}))))
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_live()))
