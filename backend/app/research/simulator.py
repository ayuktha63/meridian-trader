"""An order's life on bars, for strategies studied offline.

The engine's own backtest (`backtest/engine.py`) simulates exactly one kind
of order: the live one — a limit at the signal price with a fixed stop and
target. Other families need a market entry at the next open, an entry on a
break, an exit that trails or follows a signal. This simulator covers those
with the same rules of evidence:

* A decision made at a bar's close can act no earlier than the next bar.
* A market order fills at the next open. A resting limit fills only when the
  market trades *through* it — at the open if it gapped in its favour. An
  entry stop triggers on a touch and fills at the open if the market gapped
  past it, which is worse.
* A protective stop triggers on a touch and fills at the open on a gap.
  When one bar reaches both stop and target, the stop is taken.
* An order filled inside a bar knows nothing about the rest of that bar
  except that the stop may have been hit; its target is not credited there.
* Every trade is charged by `app/costs.py` under a named venue profile.

R is the risk *planned* at the signal: decision price to initial stop. The
result is measured from the actual fill, so a gap between the decision and
the open costs R, as it would.

Where the two simulators overlap — a limit entry with a fixed stop and
target — a test holds them to the same trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .. import costs
from ..analysis import indicators as ind
from ..instruments import Instrument, get_timeframe

MARKET, LIMIT, STOP = "market", "limit", "stop"


@dataclass(frozen=True)
class Signal:
    """A trade decided at a bar's close."""
    direction: int                      # +1 long, -1 short
    stop: float                         # initial protective stop, a price
    target: Optional[float] = None
    entry: str = MARKET
    price: Optional[float] = None       # the limit, or the entry stop's trigger
    valid_bars: int = 1                 # how long a resting entry stays working
    trail_atr: Optional[float] = None   # chandelier exit, in ATRs; None for none
    max_hold: Optional[int] = None      # bars
    tag: str = ""


@dataclass
class Trade:
    signal_index: int
    entry_index: int
    exit_index: int
    entry_time: float
    exit_time: float
    direction: int
    entry: float
    exit: float
    initial_stop: float
    risk: float
    gross_r: float
    cost_r: float
    r: float
    bars_held: int
    reason: str
    mae_r: float
    mfe_r: float
    tag: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Bars:
    """A market's history as plain lists — the loop below reads one value at
    a time, where a list is several times quicker than an array."""
    time: List[float]
    open: List[float]
    high: List[float]
    low: List[float]
    close: List[float]
    atr: List[float]
    tf_seconds: int

    def __len__(self) -> int:
        return len(self.time)

    @classmethod
    def from_frame(cls, frame: pd.DataFrame, timeframe: str) -> "Bars":
        atr = ind.atr(frame, 14).to_numpy(dtype=float)
        return cls(time=frame["time"].tolist(), open=frame["open"].tolist(),
                   high=frame["high"].tolist(), low=frame["low"].tolist(),
                   close=frame["close"].tolist(), atr=np.nan_to_num(atr).tolist(),
                   tf_seconds=get_timeframe(timeframe).seconds)


@dataclass
class _Position:
    signal: Signal
    signal_index: int
    index: int
    entry: float
    stop: float
    risk: float
    extreme: float                      # best price since entry, for the trail
    worst: float
    best: float
    trailed: bool = False
    leave_at_open: bool = False


def reference(signal: Signal, close: float) -> float:
    """The price the trade was planned at: the close for a market order,
    the order's own price otherwise."""
    return close if signal.entry == MARKET or signal.price is None else signal.price


def sane(signal: Signal, close: float) -> bool:
    """Stop and target on the right sides of the planned entry."""
    ref = reference(signal, close)
    if signal.direction not in (1, -1) or ref <= 0 or signal.stop <= 0:
        return False
    if (ref - signal.stop) * signal.direction <= 0:
        return False
    return signal.target is None or (signal.target - ref) * signal.direction > 0


def _still_the_trade(signal: Signal, fill: float) -> bool:
    """Whether a fill at this price is still the trade that was planned.

    A market order is checked before it is sent, so one that would open
    beyond its own stop is not sent. A resting order cannot be: it fills
    where the market opens, and the stop then takes it out at that price —
    which is what the live order does. Beyond the target, nothing is left
    of the trade either way.
    """
    direction = signal.direction
    if signal.target is not None and (signal.target - fill) * direction <= 0:
        return False
    return signal.entry != MARKET or (fill - signal.stop) * direction > 0


def _limit_fill(long: bool, level: float, bar_open: float, high: float,
                low: float) -> Optional[Tuple[float, bool]]:
    if (bar_open <= level) if long else (bar_open >= level):
        return bar_open, False                      # gapped through, in its favour
    if (low < level) if long else (high > level):
        return level, True
    return None


def _stop_entry_fill(long: bool, level: float, bar_open: float, high: float,
                     low: float) -> Optional[Tuple[float, bool]]:
    if (bar_open >= level) if long else (bar_open <= level):
        return bar_open, False                      # gapped past the trigger: worse
    if (high >= level) if long else (low <= level):
        return level, True
    return None


def _entry_fill(signal: Signal, bars: Bars, i: int) -> Optional[Tuple[float, bool]]:
    """(fill price, filled inside the bar) or None."""
    bar_open, high, low = bars.open[i], bars.high[i], bars.low[i]
    if signal.entry == MARKET:
        return bar_open, False
    fill = _limit_fill if signal.entry == LIMIT else _stop_entry_fill
    return fill(signal.direction > 0, signal.price, bar_open, high, low)


def _exit(position: _Position, bars: Bars, i: int,
          filled_inside: bool) -> Optional[Tuple[float, str]]:
    bar_open, high, low = bars.open[i], bars.high[i], bars.low[i]
    signal, long = position.signal, position.signal.direction > 0
    if position.leave_at_open and not filled_inside:
        return bar_open, "signal"
    stop = position.stop
    hit = low <= stop if long else high >= stop
    if hit:
        gapped = (bar_open <= stop) if long else (bar_open >= stop)
        if gapped and not filled_inside:
            return bar_open, "gap"
        return stop, "trail" if position.trailed else "stop"
    target = signal.target
    if target is not None and not filled_inside:
        if (high > target) if long else (low < target):
            return target, "target"
    if signal.max_hold is not None and i - position.index >= signal.max_hold:
        return bars.close[i], "time"
    return None


def _carry_forward(position: _Position, bars: Bars, i: int,
                   leave: Optional[Sequence[bool]]) -> None:
    """End of a bar the position survived: what it has seen, where the stop
    trails to, and whether the strategy wants out at the next open."""
    long = position.signal.direction > 0
    high, low = bars.high[i], bars.low[i]
    position.best = max(position.best, high) if long else min(position.best, low)
    position.worst = min(position.worst, low) if long else max(position.worst, high)
    position.extreme = position.best
    multiple, atr = position.signal.trail_atr, bars.atr[i]
    if multiple and atr > 0:
        level = position.extreme - multiple * atr if long else position.extreme + multiple * atr
        if (level > position.stop) if long else (level < position.stop):
            position.stop, position.trailed = level, True
    if leave is not None and leave[i]:
        position.leave_at_open = True


def _settle(position: _Position, bars: Bars, i: int, price: float, reason: str,
            instrument: Instrument, profile: Optional[str], cost_multiplier: float,
            include_costs: bool) -> Trade:
    signal, direction = position.signal, position.signal.direction
    long = direction > 0
    # The bar it left on counts towards the worst it saw, up to the exit.
    adverse = min(position.worst, bars.low[i], price) if long else max(
        position.worst, bars.high[i], price)
    favourable = position.best
    cost = 0.0
    if include_costs:
        cost = costs.round_trip_cost(
            instrument, position.entry, price, stopped=reason in ("stop", "gap", "trail"),
            entry_passive=signal.entry == LIMIT, exit_passive=reason == "target",
            profile=profile, seconds_held=bars.time[i] - bars.time[position.index])
        if signal.entry == STOP:                     # a triggered entry slips as well
            cost += costs.stop_slippage(instrument, position.entry, profile)
        cost *= cost_multiplier
    risk = position.risk
    gross = direction * (price - position.entry) / risk
    return Trade(
        signal_index=position.signal_index, entry_index=position.index, exit_index=i,
        entry_time=bars.time[position.index], exit_time=bars.time[i], direction=direction,
        entry=position.entry, exit=price, initial_stop=signal.stop, risk=risk,
        gross_r=gross, cost_r=cost / risk, r=gross - cost / risk,
        bars_held=i - position.index, reason=reason,
        mae_r=min(direction * (adverse - position.entry) / risk, 0.0),
        mfe_r=max(direction * (favourable - position.entry) / risk, 0.0), tag=signal.tag)


@dataclass
class _Run:
    """One pass over a market: what is resting, what is open, what is done."""
    bars: Bars
    signals: Mapping[int, Signal]
    instrument: Instrument
    profile: Optional[str]
    cost_multiplier: float
    include_costs: bool
    exit_long: Optional[Sequence[bool]]
    exit_short: Optional[Sequence[bool]]
    shorting: bool
    position: Optional[_Position] = None
    pending: Optional[Tuple[Signal, int]] = None

    def __post_init__(self) -> None:
        self.trades: List[Trade] = []

    def work_entry(self, i: int) -> bool:
        """Try to fill what is resting. True if it filled inside this bar."""
        if self.pending is None:
            return False
        signal, placed = self.pending
        fill = _entry_fill(signal, self.bars, i)
        if fill is None:
            if i - placed >= signal.valid_bars:
                self.pending = None
            return False
        self.pending = None
        price, inside = fill
        if _still_the_trade(signal, price):
            self.position = _Position(
                signal=signal, signal_index=placed, index=i, entry=price, stop=signal.stop,
                risk=abs(reference(signal, self.bars.close[placed]) - signal.stop),
                extreme=price, worst=price, best=price)
        return inside

    def manage(self, i: int, filled_inside: bool) -> None:
        position = self.position
        done = _exit(position, self.bars, i, filled_inside)
        if done is None:
            leave = self.exit_long if position.signal.direction > 0 else self.exit_short
            _carry_forward(position, self.bars, i, leave)
            return
        self.close(i, done[0], done[1])

    def close(self, i: int, price: float, reason: str) -> None:
        self.trades.append(_settle(self.position, self.bars, i, price, reason,
                                   self.instrument, self.profile, self.cost_multiplier,
                                   self.include_costs))
        self.position = None

    def consider(self, i: int) -> None:
        """A decision at this bar's close, to act on the next."""
        signal = self.signals.get(i)
        if (signal is not None and sane(signal, self.bars.close[i])
                and (signal.direction > 0 or self.shorting)):
            self.pending = (signal, i)


def simulate(bars: Bars, signals: Mapping[int, Signal], instrument: Instrument, *,
             start: int = 0, end: Optional[int] = None, profile: Optional[str] = None,
             cost_multiplier: float = 1.0, include_costs: bool = True,
             exit_long: Optional[Sequence[bool]] = None,
             exit_short: Optional[Sequence[bool]] = None) -> List[Trade]:
    """Trade bars [start, end). One position at a time; a signal that arrives
    while one is open or an entry is resting is not taken — nor is one on
    the bar a position was closed on."""
    n = len(bars) if end is None else min(end, len(bars))
    run = _Run(bars, signals, instrument, profile, cost_multiplier, include_costs,
               exit_long, exit_short, costs.can_short(instrument, profile))
    for i in range(start, n):
        filled_inside = run.work_entry(i)
        if run.position is not None:
            run.manage(i, filled_inside)
        elif run.pending is None and i + 1 < n:
            run.consider(i)
    if run.position is not None and n > start:
        # Still open when the window ends: marked at its last close, so a
        # window's result does not depend on trades it never finished.
        run.close(n - 1, bars.close[n - 1], "end")
    return run.trades


def r_values(trades: Sequence[Trade]) -> np.ndarray:
    return np.array([t.r for t in trades], dtype=float)
