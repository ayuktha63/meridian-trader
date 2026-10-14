"""A simulated trade, charged as the orders it would have been.

The simulator (`simulator.py`) works in R — profit over planned risk, per
share — and prices a trade with a rate. For Indian equities a rate is not
enough (`costs_india.py`): the bill has parts that do not scale with the
order, so what a trade costs depends on how many shares it was, and that
depends on whose account it is.

So this stage takes trades the simulator produced *without* costs and does
what the simulator cannot:

1. **Sizes each one against an account** — whole shares, the loss at the
   stop including costs inside the risk budget, never rounded up. A signal
   the account cannot afford one share of is not a trade; it is counted as
   skipped, with the reason.
2. **Charges it as real orders** — an entry and an exit, brokerage with its
   floor and cap, each statutory charge by name, the depository charge, and
   an estimate of what the book takes.
3. **Reports it gross and net**, in rupees and in R, so that a result says
   what was made before costs, what each kind of cost took, and what was left.

The same trades can be charged under any account, execution scenario or
cost multiple, which is what makes "does it survive costs half as large
again" and "what does a small account actually get" questions with answers.

Two limits, stated: sizing uses the history's prices, which are adjusted
for splits, so a stock that has since split looks cheaper per share in its
past than it was — a small account is shown affording shares it could not
have. And the account does not compound: every trade is sized against the
same equity, so the result is a rate per trade, not a growth curve.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .. import costs_india
from ..costs_india import DELIVERY, INTRADAY, TradeCost
from .exchange_calendar import NSE_EQUITY, Session
from .simulator import LIMIT, MARKET, STOP, Trade

#: Exits the simulator makes with a triggered stop order.
STOPPED = ("stop", "gap", "trail")


@dataclass(frozen=True)
class Account:
    """Whose trades these are: how much there is, and how it is risked."""
    equity: float
    risk_fraction: float = 0.005
    product: str = DELIVERY
    #: Buying power as a multiple of equity. One for delivery: shares are
    #: paid for in full.
    leverage: float = 1.0
    max_notional: Optional[float] = None
    scenario: str = costs_india.ELIGIBILITY_SCENARIO

    def describe(self) -> Dict[str, Any]:
        return {"equity": self.equity, "risk_fraction": self.risk_fraction,
                "product": self.product, "leverage": self.leverage,
                "max_notional": self.max_notional, "scenario": self.scenario,
                "compounding": False}


@dataclass(frozen=True)
class Costed:
    """One simulated trade as an account would have had it."""
    trade: Trade
    quantity: int
    #: Rupees at risk as planned: shares times the distance to the stop.
    risk: float
    cost: TradeCost

    @property
    def gross(self) -> float:
        return self.cost.gross

    @property
    def net(self) -> float:
        return self.cost.net

    @property
    def gross_r(self) -> float:
        return self.cost.gross / self.risk

    @property
    def cost_r(self) -> float:
        return self.cost.costs / self.risk

    @property
    def r(self) -> float:
        return self.cost.net / self.risk


@dataclass
class Run:
    account: Account
    multiplier: float
    taken: List[Costed] = field(default_factory=list)
    #: Signals the account could not trade: (trade, why).
    skipped: List[Tuple[Trade, str]] = field(default_factory=list)

    @property
    def r(self) -> np.ndarray:
        return np.array([c.r for c in self.taken], dtype=float)


def from_engine(trades: Sequence[Any]) -> List[Trade]:
    """The live engine's backtest trades (`schemas.journal.BacktestTrade`)
    as trades this stage can charge.

    What the engine charged is dropped — it priced the trade with a rate per
    side — and only what happened is kept: where it got in and out, where
    its stop was set, and why it ended. A trade still open is left out.

    The engine enters with a resting limit, so charge the result with
    `entry_kind=LIMIT`. Its exits are "stop", "target" (a resting order, no
    spread), and "eod" and "timeout", which cross the spread. The planned
    risk is taken as fill to stop: the engine fills at its limit or better,
    so this is the risk the position actually carried.
    """
    out: List[Trade] = []
    for trade in trades:
        if trade.exit_price is None or trade.exit_time is None:
            continue
        direction = 1 if str(trade.direction).upper() == "BUY" else -1
        risk = abs(float(trade.entry) - float(trade.stop_loss))
        if risk <= 0:
            continue
        gross = direction * (float(trade.exit_price) - float(trade.entry)) / risk
        out.append(Trade(
            signal_index=int(trade.index), entry_index=int(trade.index),
            exit_index=int(trade.index) + int(trade.bars_held),
            entry_time=float(trade.time), exit_time=float(trade.exit_time),
            direction=direction, entry=float(trade.entry), exit=float(trade.exit_price),
            initial_stop=float(trade.stop_loss), risk=risk, gross_r=gross, cost_r=0.0,
            r=gross, bars_held=int(trade.bars_held),
            reason=trade.exit_reason or trade.outcome, mae_r=0.0, mfe_r=0.0, tag="engine"))
    return out


def _same_session(trade: Trade, session: Session) -> bool:
    days = session.local_day([trade.entry_time, trade.exit_time])
    return bool(days[0] == days[1])


def cost_trades(trades: Sequence[Trade], account: Account, *, entry_kind: str = MARKET,
                multiplier: float = 1.0, charged_as: Optional[str] = None,
                session: Session = NSE_EQUITY) -> Run:
    """Size and charge every trade for this account.

    `trades` come from `simulator.simulate(..., include_costs=False)`: what
    is charged here must not have been charged there as well. `entry_kind` is
    the order the strategy enters with — a limit that rested pays no spread,
    an entry stop slips on the trigger.

    The position is always sized by the account's own scenario. `charged_as`
    and `multiplier` change what those same shares are then charged — a
    stress asks what the trades that were taken would have made, not what
    else would have been traded.
    """
    if entry_kind not in (MARKET, LIMIT, STOP):
        raise ValueError("An entry is market, limit or stop, not {!r}.".format(entry_kind))
    rates = costs_india.schedule(account.product)
    charged_as = charged_as or account.scenario
    run = Run(account=account, multiplier=multiplier)
    for trade in trades:
        if trade.cost_r:
            raise ValueError("This trade was already charged by the simulator "
                             "(cost_r = {:.4f}); it would be charged twice.".format(trade.cost_r))
        long = trade.direction > 0
        if not long and not rates.shorting:
            run.skipped.append((trade, "A {} position cannot be opened short.".format(
                account.product)))
            continue
        if account.product == INTRADAY and not _same_session(trade, session):
            raise ValueError(
                "A trade held from {} to {} is not an intraday trade: the broker squares "
                "an intraday position off at the close, and its charges are not these."
                .format(Session.iso(int(session.local_day([trade.entry_time])[0])),
                        Session.iso(int(session.local_day([trade.exit_time])[0]))))
        # Sized at the decision, before the fill is known: the price the stop
        # was set from, not the open the order happened to get.
        decided = trade.initial_stop + trade.direction * trade.risk
        sized = costs_india.size_position(
            account.equity, account.risk_fraction, decided, trade.initial_stop,
            product=account.product, long=long, scenario=account.scenario,
            leverage=account.leverage, max_notional=account.max_notional)
        if sized.quantity <= 0:
            run.skipped.append((trade, sized.reason or "Could not be sized."))
            continue
        cost = costs_india.trade_cost(
            sized.quantity, trade.entry, trade.exit, product=account.product, long=long,
            scenario=charged_as, stopped=trade.reason in STOPPED, multiplier=multiplier,
            entry_passive=entry_kind == LIMIT, exit_passive=trade.reason == "target",
            entry_triggered=entry_kind == STOP)
        run.taken.append(Costed(trade=trade, quantity=sized.quantity,
                                risk=sized.quantity * trade.risk, cost=cost))
    return run


def summary(run: Run) -> Dict[str, Any]:
    """Gross to net for the whole run, every cost by name."""
    taken = run.taken
    out: Dict[str, Any] = {
        "account": run.account.describe(), "multiplier": run.multiplier,
        "trades": len(taken), "skipped": len(run.skipped),
        "schedule": costs_india.schedule(run.account.product).id,
        "costs_version": costs_india.fingerprint(),
    }
    if run.skipped:
        reasons: Dict[str, int] = {}
        for _, why in run.skipped:
            key = why.split(" at the stop")[0].split(" and there is")[0]
            reasons[key] = reasons.get(key, 0) + 1
        out["skipped_because"] = reasons
    if not taken:
        return out
    gross = sum(c.gross for c in taken)
    lines = {name: sum(getattr(c.cost.charges, name) for c in taken)
             for name in ("brokerage", "dp", "stt", "stamp", "exchange", "sebi", "ipft", "gst")}
    execution = sum(c.cost.execution for c in taken)
    costs = sum(c.cost.costs for c in taken)
    turnover = sum(c.cost.charges.turnover for c in taken)
    r = run.r
    out.update({
        "gross_pnl": round(gross, 2), "net_pnl": round(gross - costs, 2),
        "total_costs": round(costs, 2),
        "broker_fees": round(lines["brokerage"] + lines["dp"], 2),
        "statutory_charges": round(costs - execution - lines["brokerage"] - lines["dp"], 2),
        "execution_estimate": round(execution, 2),
        "charges": {name: round(value, 2) for name, value in lines.items()},
        "turnover": round(turnover, 2),
        "cost_per_trade": round(costs / len(taken), 2),
        "cost_pct_of_turnover": round(100.0 * costs / turnover, 4) if turnover else None,
        "expectancy_rupees": round((gross - costs) / len(taken), 2),
        "expectancy_r": round(float(r.mean()), 4),
        "gross_expectancy_r": round(float(np.mean([c.gross_r for c in taken])), 4),
        "cost_r": round(float(np.mean([c.cost_r for c in taken])), 4),
        "win_rate": round(float((r > 0).mean()), 4),
        # How many times the modelled costs this result could bear before
        # nothing was left — the number that survives being wrong about them.
        "break_even_cost_multiple": (round(run.multiplier * gross / costs, 2)
                                     if costs > 0 and gross > 0 else 0.0),
    })
    return out


def sensitivity(trades: Sequence[Trade], account: Account, *, entry_kind: str = MARKET,
                multipliers: Sequence[float] = (1.0,) + costs_india.STRESS_MULTIPLIERS,
                scenarios: Sequence[str] = ("optimistic", "base", "conservative"),
                session: Session = NSE_EQUITY) -> List[Dict[str, Any]]:
    """Net expectancy under each execution scenario and cost multiple, on
    the same shares every time."""
    rows = []
    for scenario in scenarios:
        for multiplier in multipliers:
            run = cost_trades(trades, account, entry_kind=entry_kind, multiplier=multiplier,
                              charged_as=scenario, session=session)
            r = run.r
            rows.append({"scenario": scenario, "multiplier": multiplier,
                         "trades": len(run.taken), "skipped": len(run.skipped),
                         "expectancy_r": round(float(r.mean()), 4) if len(r) else None,
                         "net_pnl": round(sum(c.net for c in run.taken), 2)})
    return rows


def by_account(trades: Sequence[Trade], equities: Sequence[float], *,
               risk_fraction: float = 0.005, product: str = DELIVERY,
               entry_kind: str = MARKET, leverage: float = 1.0) -> List[Dict[str, Any]]:
    """The same trades for accounts of different sizes: how many each could
    take, and what each kept."""
    rows = []
    for equity in equities:
        run = cost_trades(trades, Account(equity, risk_fraction, product, leverage),
                          entry_kind=entry_kind)
        r = run.r
        rows.append({"equity": equity, "trades": len(run.taken), "skipped": len(run.skipped),
                     "expectancy_r": round(float(r.mean()), 4) if len(r) else None,
                     "cost_r": (round(float(np.mean([c.cost_r for c in run.taken])), 4)
                                if run.taken else None)})
    return rows
