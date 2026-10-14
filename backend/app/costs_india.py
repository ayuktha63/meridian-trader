"""What a trade in NSE cash equities costs, order by order.

`costs.py` prices a trade as a rate per side. That is the right shape for a
fee that is a percentage and nothing else, and the wrong one here, for a
reason that decides whether a small account can trade at all: three of the
charges on an Indian equity order are not percentages.

* Brokerage is "the lower of Rs 20 and 0.1%, minimum Rs 5" *per executed
  order*. On a Rs 2,000 order that is Rs 5 — a quarter of a percent, two and
  a half times the headline rate.
* The depository charge on a delivery sale is a flat Rs 20 plus GST per
  stock per day, whether ten shares were sold or ten thousand.
* GST is charged on those, so it carries the same floor.

A rate cannot hold a floor. So this module prices an *order* — a side, a
value, a product — and returns every charge by name, and a round trip is two
or more orders added up.

Three kinds of cost are kept apart, because they are known to different
degrees:

* **Statutory and exchange charges** — STT, stamp duty, the exchange's
  transaction charge, SEBI's fee, the investor-protection levy. Set by law
  and by circular; the same at every broker.
* **Broker and depository fees** — brokerage and the DP charge. Set by Angel
  One's price list, which it changes (last on 17 November 2025).
* **Execution costs** — spread and slippage. Not charges at all: an
  *estimate* of the price given up to get filled. Nobody publishes them.
  `EXECUTION` holds three sets of assumptions, and they are assumptions
  until fills from paper trading replace them.

Every rate is in a `Schedule` with a date and a source. A schedule is the
broker's published list as read on the day it names — checked against Angel
One's own charge estimator where a session allows (`verify`), and not yet
against a contract note. A result records which schedule charged it.

Not modelled, and said here: contract-note rounding (STT is rounded to the
rupee per day's note), SEBI's 2.5% ceiling on brokerage for very small
orders, auction penalties on a failed delivery, margin-funding interest, and
tax on the gains.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

BUY, SELL = "BUY", "SELL"
DELIVERY, INTRADAY = "delivery", "intraday"

#: What the eligibility protocol asks a result to survive.
STRESS_MULTIPLIERS = (1.5, 2.0)


@dataclass(frozen=True)
class Schedule:
    """One product's charges, as fractions of order value unless said."""
    id: str
    broker: str
    exchange: str
    product: str
    effective_from: str
    retrieved: str
    #: Brokerage per executed order: `rate` of its value, at least
    #: `minimum` and at most `cap` rupees.
    brokerage_rate: float
    brokerage_cap: float
    brokerage_minimum: float
    stt_buy: float
    stt_sell: float
    exchange_transaction: float
    sebi: float
    ipft: float
    stamp_buy: float
    #: GST on brokerage, the exchange and SEBI charges, the levy and the DP
    #: charge — not on STT or stamp duty.
    gst: float
    #: Rupees per stock per day on which any of it was sold from the demat
    #: account, before GST. Nothing on an intraday trade: no shares move.
    dp_per_sell_day: float
    #: Whether a position can be opened by selling.
    shorting: bool
    sources: Tuple[str, ...]
    verified: str

    def describe(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


_ANGEL_SOURCES = (
    "Angel One, list of charges — https://www.angelone.in/list-of-charges (read 2026-10-13)",
    "Angel One, pricing update effective 17 Nov 2025 — "
    "https://www.angelone.in/news/product-updates/pricing-update-nov-2025-b2c",
    "STT: Finance Act rates; equity delivery and intraday unchanged by the 2026 Budget, "
    "which raised futures and options only (effective 1 Apr 2026)",
)
_VERIFIED = ("read from the broker's published price list on the retrieval date, and found "
             "to agree to the paisa with Angel One's own charge estimator on 2026-10-13 for "
             "buys and sells of Rs 1,340, Rs 13,400 and Rs 268,000 (data/angel_charges.py) — "
             "every line but the DP charge, which that estimator does not include and which "
             "rests on the price list alone. Not yet reconciled with a contract note.")

SCHEDULES: Dict[str, Schedule] = {
    DELIVERY: Schedule(
        id="angelone-nse-equity-delivery-2025-11-17", broker="Angel One", exchange="NSE",
        product=DELIVERY, effective_from="2025-11-17", retrieved="2026-10-13",
        brokerage_rate=0.001, brokerage_cap=20.0, brokerage_minimum=5.0,
        stt_buy=0.001, stt_sell=0.001,
        exchange_transaction=0.000030699, sebi=0.000001, ipft=0.000000001,
        stamp_buy=0.00015, gst=0.18, dp_per_sell_day=20.0, shorting=False,
        sources=_ANGEL_SOURCES, verified=_VERIFIED),
    INTRADAY: Schedule(
        id="angelone-nse-equity-intraday-2025-11-17", broker="Angel One", exchange="NSE",
        product=INTRADAY, effective_from="2025-11-17", retrieved="2026-10-13",
        brokerage_rate=0.001, brokerage_cap=20.0, brokerage_minimum=5.0,
        stt_buy=0.0, stt_sell=0.00025,
        exchange_transaction=0.000030699, sebi=0.000001, ipft=0.000000001,
        stamp_buy=0.00003, gst=0.18, dp_per_sell_day=0.0, shorting=True,
        sources=_ANGEL_SOURCES, verified=_VERIFIED),
}


def schedule(product: str) -> Schedule:
    try:
        return SCHEDULES[product]
    except KeyError:
        raise KeyError("No schedule for {!r}; there is one for each of {}.".format(
            product, ", ".join(SCHEDULES))) from None


# ── charges on an order ───────────────────────────────────────

@dataclass(frozen=True)
class Charges:
    """Every charge on one order, or on several added together, in rupees."""
    turnover: float = 0.0
    brokerage: float = 0.0
    dp: float = 0.0
    stt: float = 0.0
    stamp: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    ipft: float = 0.0
    gst: float = 0.0

    @property
    def broker(self) -> float:
        """What the broker and the depository charge."""
        return self.brokerage + self.dp

    @property
    def statutory(self) -> float:
        """What the state, the exchange and the regulator charge, GST included."""
        return self.stt + self.stamp + self.exchange + self.sebi + self.ipft + self.gst

    @property
    def total(self) -> float:
        return self.broker + self.statutory

    def __add__(self, other: "Charges") -> "Charges":
        return Charges(**{f.name: getattr(self, f.name) + getattr(other, f.name)
                          for f in fields(self)})

    def scaled(self, multiplier: float) -> "Charges":
        """Every charge multiplied — a stress test, not a schedule."""
        return Charges(turnover=self.turnover, **{
            f.name: getattr(self, f.name) * multiplier
            for f in fields(self) if f.name != "turnover"})

    def to_dict(self) -> Dict[str, float]:
        out = {f.name: round(getattr(self, f.name), 4) for f in fields(self)}
        out.update(broker=round(self.broker, 4), statutory=round(self.statutory, 4),
                   total=round(self.total, 4))
        return out


def brokerage(rates: Schedule, value: float) -> float:
    """Brokerage on one executed order of this value. An order that filled in
    five pieces is still one order, and is charged once on the whole of it."""
    if value <= 0:
        return 0.0
    return min(rates.brokerage_cap, max(rates.brokerage_minimum, rates.brokerage_rate * value))


def order_charges(rates: Schedule, side: str, value: float, *,
                  debits_demat: Optional[bool] = None) -> Charges:
    """The charges on one executed order.

    `value` is what actually traded — price times the quantity filled, so a
    partly filled order is charged on the part that filled, and an order that
    was rejected or never filled, on nothing. `debits_demat` says whether
    this sale is the day's first of this stock out of the demat account,
    which is what the DP charge is levied on; by default a delivery sale is.
    """
    side = side.upper()
    if side not in (BUY, SELL):
        raise ValueError("An order is a BUY or a SELL, not {!r}.".format(side))
    value = abs(float(value))
    if value == 0.0:
        return Charges()
    fee = brokerage(rates, value)
    if debits_demat is None:
        debits_demat = side == SELL
    dp = rates.dp_per_sell_day if (side == SELL and debits_demat) else 0.0
    exchange, sebi, ipft = (value * rates.exchange_transaction, value * rates.sebi,
                            value * rates.ipft)
    return Charges(
        turnover=value, brokerage=fee, dp=dp,
        stt=value * (rates.stt_buy if side == BUY else rates.stt_sell),
        stamp=value * rates.stamp_buy if side == BUY else 0.0,
        exchange=exchange, sebi=sebi, ipft=ipft,
        gst=rates.gst * (fee + dp + exchange + sebi + ipft))


def round_trip_charges(rates: Schedule, entry_value: float, exit_values: Sequence[float], *,
                       long: bool = True, sell_days: Optional[int] = None) -> Charges:
    """The charges on a position opened with one order and closed with one or
    more. Each exit is its own order, with its own brokerage and its own
    floor; the DP charge is per day on which shares left the account, so
    `sell_days` (by default one per exit) is what it is counted on."""
    if not long and not rates.shorting:
        raise ValueError("A {} position cannot be opened short: there is nothing to "
                         "deliver.".format(rates.product))
    exits = [abs(float(v)) for v in exit_values if v]
    opening, closing = (BUY, SELL) if long else (SELL, BUY)
    total = order_charges(rates, opening, entry_value, debits_demat=False)
    days = len(exits) if sell_days is None else max(0, min(int(sell_days), len(exits)))
    for index, value in enumerate(exits):
        total = total + order_charges(rates, closing, value,
                                      debits_demat=closing == SELL and index < days)
    return total


# ── execution: what is given up to get filled ─────────────────

@dataclass(frozen=True)
class Execution:
    """Assumed, not measured. Fractions of price."""
    name: str
    #: Half the bid-ask spread, paid by each order that crosses it.
    half_spread: float
    #: Slippage beyond the spread on a market order: the size ahead of you.
    slippage: float
    #: Further slippage when the exit is a triggered stop, which sells into
    #: whatever the book holds at that moment.
    stop_slippage: float
    basis: str

    def describe(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


_BASIS = ("assumed for Nifty 50 stocks, whose spread is usually one or two ticks (about a "
          "basis point); not measured — to be replaced by fills from paper trading")

EXECUTION: Dict[str, Execution] = {
    "optimistic": Execution("optimistic", 0.0001, 0.0, 0.0002, _BASIS),
    "base": Execution("base", 0.0002, 0.0002, 0.0005, _BASIS),
    # The one a result has to survive to be eligible.
    "conservative": Execution("conservative", 0.0005, 0.0005, 0.0015, _BASIS),
}
ELIGIBILITY_SCENARIO = "conservative"


def execution_cost(assumed: Execution, quantity: float, entry: float, exit_price: float, *,
                   stopped: bool = False, entry_passive: bool = False,
                   exit_passive: bool = False, entry_triggered: bool = False) -> float:
    """Rupees given up to the book on a round trip. A resting limit order
    that the market came to pays neither spread nor slippage — what it pays
    is in which orders fill at all, and that is the simulator's to model. An
    entry that was itself a triggered stop — a buy on a break — slips as a
    protective stop does."""
    per_side = assumed.half_spread + assumed.slippage
    entry_cost = 0.0 if entry_passive else abs(entry) * per_side
    exit_cost = 0.0 if exit_passive else abs(exit_price) * per_side
    if stopped:
        exit_cost += abs(exit_price) * assumed.stop_slippage
    if entry_triggered:
        entry_cost += abs(entry) * assumed.stop_slippage
    return abs(quantity) * (entry_cost + exit_cost)


# ── a whole trade ─────────────────────────────────────────────

@dataclass(frozen=True)
class TradeCost:
    """One round trip, gross to net, with nothing folded together."""
    quantity: float
    gross: float
    charges: Charges
    execution: float
    schedule_id: str
    execution_scenario: str
    multiplier: float

    @property
    def costs(self) -> float:
        return self.charges.total + self.execution

    @property
    def net(self) -> float:
        return self.gross - self.costs

    def to_dict(self) -> Dict[str, Any]:
        return {"quantity": self.quantity, "gross": round(self.gross, 2),
                "net": round(self.net, 2), "costs": round(self.costs, 2),
                "execution": round(self.execution, 2), "charges": self.charges.to_dict(),
                "turnover": round(self.charges.turnover, 2),
                "schedule": self.schedule_id, "execution_scenario": self.execution_scenario,
                "multiplier": self.multiplier}


def trade_cost(quantity: float, entry: float, exit_price: float, *, product: str = DELIVERY,
               long: bool = True, scenario: str = ELIGIBILITY_SCENARIO, stopped: bool = False,
               multiplier: float = 1.0, exits: Optional[Sequence[Tuple[float, float]]] = None,
               sell_days: Optional[int] = None, entry_passive: bool = False,
               exit_passive: bool = False, entry_triggered: bool = False) -> TradeCost:
    """Gross and net for one position.

    `exits` is [(quantity, price), …] when the position was closed in parts;
    otherwise all of it left at `exit_price`. `multiplier` scales every cost
    — charges and execution alike — and is how a result is asked whether it
    survives costs half as large again as the ones modelled.
    """
    rates, assumed = schedule(product), EXECUTION[scenario]
    quantity = abs(float(quantity))
    legs = list(exits) if exits else [(quantity, exit_price)]
    closed = sum(abs(q) for q, _ in legs)
    if closed > quantity * (1 + 1e-9):
        raise ValueError("Exits close {:g} of a position of {:g}.".format(closed, quantity))
    direction = 1.0 if long else -1.0
    gross = sum(direction * abs(q) * (price - entry) for q, price in legs)
    charges = round_trip_charges(rates, quantity * entry, [abs(q) * p for q, p in legs],
                                 long=long, sell_days=sell_days)
    given_up = sum(
        execution_cost(assumed, q, entry, price, stopped=stopped,
                       entry_passive=entry_passive, exit_passive=exit_passive,
                       entry_triggered=entry_triggered)
        for q, price in legs)
    return TradeCost(quantity=quantity, gross=gross, charges=charges.scaled(multiplier),
                     execution=given_up * multiplier, schedule_id=rates.id,
                     execution_scenario=assumed.name, multiplier=multiplier)


# ── what that does to a small account ─────────────────────────

@dataclass(frozen=True)
class Sizing:
    quantity: int
    risk_budget: float
    #: What is lost if the stop is hit at its price: the move, plus every
    #: cost of getting in and out.
    loss_at_stop: float
    costs_at_stop: float
    notional: float
    #: Why the quantity is zero, when it is.
    reason: Optional[str] = None

    @property
    def cost_in_r(self) -> Optional[float]:
        """Round-trip costs as a share of the risk budget: what a trade must
        make, in R, before it has made anything."""
        return self.costs_at_stop / self.risk_budget if self.quantity else None


def size_position(equity: float, risk_fraction: float, entry: float, stop: float, *,
                  product: str = DELIVERY, long: bool = True,
                  scenario: str = ELIGIBILITY_SCENARIO, leverage: float = 1.0,
                  max_notional: Optional[float] = None) -> Sizing:
    """The largest whole number of shares whose loss at the stop — the move
    *and* the costs — fits inside the risk budget and the cash there is.

    Never rounded up: if one share cannot be afforded inside the budget, the
    answer is none, with the reason. A stop is not a guarantee either — a gap
    fills beyond it — so this is the planned loss, not the worst one.
    """
    budget = equity * risk_fraction
    distance = abs(entry - stop)
    if equity <= 0 or budget <= 0 or entry <= 0 or distance <= 0:
        return Sizing(0, max(budget, 0.0), 0.0, 0.0, 0.0,
                      "No equity, no risk budget, or no distance between entry and stop.")
    if (stop >= entry) if long else (stop <= entry):
        return Sizing(0, budget, 0.0, 0.0, 0.0, "The stop is on the wrong side of the entry.")
    ceiling = equity * max(leverage, 0.0) if max_notional is None else min(
        equity * max(leverage, 0.0), max_notional)

    def at_stop(quantity: int) -> Tuple[float, float]:
        cost = trade_cost(quantity, entry, stop, product=product, long=long,
                          scenario=scenario, stopped=True).costs
        return quantity * distance + cost, cost

    quantity = int(min(budget // distance, ceiling // entry))
    while quantity > 0 and at_stop(quantity)[0] > budget:
        # The fixed charges do not shrink with the order, so step down until
        # the whole loss fits — at most a few steps for any real order.
        over = at_stop(quantity)[0] - budget
        quantity -= max(1, int(over // (distance + entry * 0.003)))
    if quantity <= 0:
        one, cost = at_stop(1)
        if entry > ceiling:
            reason = "One share costs {:.2f} and there is {:.2f} to buy with.".format(
                entry, ceiling)
        else:
            reason = ("One share loses {:.2f} at the stop ({:.2f} of it charges and "
                      "slippage); the risk budget is {:.2f}.".format(one, cost, budget))
        return Sizing(0, budget, 0.0, 0.0, 0.0, reason)
    loss, cost = at_stop(quantity)
    return Sizing(quantity, budget, loss, cost, quantity * entry)


def account_table(equities: Sequence[float], *, risk_fraction: float = 0.005,
                  price: float = 1000.0, stop_pct: float = 0.05, product: str = DELIVERY,
                  scenario: str = ELIGIBILITY_SCENARIO) -> List[Dict[str, Any]]:
    """What one trade costs, in R, at each account size — the same stock, the
    same stop, the same rules. The fixed charges are why the answer changes."""
    rows = []
    for equity in equities:
        sized = size_position(equity, risk_fraction, price, price * (1 - stop_pct),
                              product=product, scenario=scenario)
        rows.append({"equity": equity, "risk_budget": round(sized.risk_budget, 2),
                     "quantity": sized.quantity, "notional": round(sized.notional, 2),
                     "costs": round(sized.costs_at_stop, 2),
                     "cost_in_r": None if sized.cost_in_r is None else round(sized.cost_in_r, 3),
                     "reason": sized.reason})
    return rows


# ── what a result records ─────────────────────────────────────

def fingerprint() -> str:
    """Names this file's contents: change a rate, and results charged by the
    old one no longer match the code that is running."""
    return "costs-india@" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]


def describe(product: str = DELIVERY, scenario: str = ELIGIBILITY_SCENARIO) -> Dict[str, Any]:
    """The assumptions a result was charged by, for its record."""
    return {"version": fingerprint(), "schedule": schedule(product).describe(),
            "execution": EXECUTION[scenario].describe(),
            "stress_multipliers": list(STRESS_MULTIPLIERS)}
