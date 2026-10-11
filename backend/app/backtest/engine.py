"""Historical replay of what actually trades.

Two things are replayed, and neither is an approximation:

* **The signal** is the live engine's own — `backtest/replay.py` runs
  `analysis.engine.evaluate` and `conclude` at every bar's close. The named
  strategies from the Plays tab come out of the same pass.
* **The order** follows the live order's life (`broker/angelone`): a LIMIT
  at the signal price that is cancelled if the market does not come back to
  it, a stop and a target, the protection ladder ratcheting the stop, and —
  for Indian exchange instruments, which are traded intraday — a square-off
  at the session's last bar and no new entry close to it.

Look-ahead discipline:

* A signal at the close of bar `i` can fill no earlier than bar `i+1`.
* A limit fills only if that bar trades through it — at the open if the
  market gapped through, at the limit otherwise. Orders that would only have
  filled by chasing are missed, which is where a limit order's adverse
  selection lives.
* On the bar a limit fills inside, nothing is known about the path after the
  fill except that the stop may have been hit; a target on that bar is not
  credited.
* When a bar's range covers both stop and target, the **stop** fills first.
* A stop the market gapped through fills at the open, not at the stop.
* Every trade pays the round trip from `app/costs.py`. Results are net.

Claude is not called during replay — the deterministic analyst supplies the
verdict, as it does live whenever Claude is off.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .. import costs
from ..analysis import indicators as ind
from ..analysis import risk as risk_engine
from ..config import settings
from ..data import sessions
from ..data.feed import feed
from ..data.store import store
from ..instruments import MTF_LADDER, Instrument, get_instrument, get_timeframe
from ..schemas.journal import (
    BacktestRequest, BacktestResult, BacktestTrade, EdgeVerdict,
)
from ..schemas.positions import Position, PositionSide
from . import metrics as metrics_mod
from . import replay
from .replay import BarSignal, Setup

log = logging.getLogger("meridian.backtest")

#: Bars of history before the first one traded — enough for a 200-period EMA.
WARMUP = 210
#: Longest a trade is held on a market that never closes.
MAX_HOLD_BARS = 200
#: Fewer out-of-sample trades than this cannot establish an edge.
MIN_EDGE_TRADES = 30

#: The fused verdict — what the trade panel shows and Execute Live sends.
ENGINE = "engine"
#: Coin-flip entries. The control: after costs this must lose.
RANDOM = "random"
PLAYS = ("trend_continuation", "mean_reversion", "breakout", "smart_money", "momentum")
STRATEGIES = (ENGINE,) + PLAYS + (RANDOM,)


@dataclass
class Prepared:
    """One market's history, ready to be traded any number of times."""
    time: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    #: Mean bar range over 20 bars — the noise scale the live ladder uses.
    range20: np.ndarray
    atr14: np.ndarray
    #: Last bar of an intraday session: whatever is open is squared off.
    session_last: np.ndarray
    #: A signal at this bar's close may still place an entry.
    entry_ok: np.ndarray
    signals: Dict[int, BarSignal]
    tf_seconds: int
    intraday: bool
    #: Whether the venue this is charged as lets a position be opened short.
    shorting: bool = True

    @property
    def n(self) -> int:
        return len(self.time)


def prepare(df: pd.DataFrame, instrument: Instrument, timeframe: str,
            signals: Optional[Dict[int, BarSignal]] = None,
            cost_profile: Optional[str] = None) -> Prepared:
    tf_seconds = get_timeframe(timeframe).seconds
    times = df["time"].to_numpy(dtype=float)
    n = len(times)
    intraday = sessions.session_key(instrument.asset_class) in sessions.INDIA_HOURS

    session_last = np.zeros(n, dtype=bool)
    entry_ok = np.ones(n, dtype=bool)
    if intraday and n > 1:
        ist_day = (times + 19_800) // 86_400
        session_last[:-1] = (np.diff(times) > 1.5 * tf_seconds) | (np.diff(ist_day) != 0)
        # Bars left in the session after this one closes. An entry needs
        # enough of them to be worth taking before the square-off.
        left = np.zeros(n)
        count = 0.0
        for i in range(n - 1, -1, -1):
            count = 0.0 if session_last[i] else count + 1.0
            left[i] = count
        left[n - 1] = np.inf           # the end of the data is not a session end
        for i in range(n - 2, -1, -1):
            if session_last[i]:
                break
            left[i] = np.inf
        window = settings.live_no_entry_minutes_before_close
        entry_ok = ~session_last & (left * tf_seconds / 60.0 >= window)

    return Prepared(
        time=times, open=df["open"].to_numpy(dtype=float),
        high=df["high"].to_numpy(dtype=float), low=df["low"].to_numpy(dtype=float),
        close=df["close"].to_numpy(dtype=float),
        range20=(df["high"] - df["low"]).rolling(20, min_periods=5).mean().to_numpy(),
        atr14=ind.atr(df, 14).to_numpy(), session_last=session_last,
        entry_ok=entry_ok, signals=signals or {}, tf_seconds=tf_seconds,
        intraday=intraday, shorting=costs.can_short(instrument, cost_profile))


# ── which trade, if any ───────────────────────────────────────

def _setup_for(request: BacktestRequest, prep: Prepared, i: int,
               rng: np.random.Generator) -> Optional[Setup]:
    if request.strategy == RANDOM:
        atr = prep.atr14[i]
        if rng.random() > 0.08 or np.isnan(atr) or atr <= 0:
            return None
        long = rng.random() < 0.5 or not prep.shorting
        entry = float(prep.close[i])
        distance = float(atr * request.atr_stop_mult)
        sign = 1.0 if long else -1.0
        return Setup("BUY" if long else "SELL", entry, entry - sign * distance,
                     entry + sign * distance * request.risk_reward, 0.0,
                     "random entry (control)")
    signal = prep.signals.get(i)
    if signal is None:
        return None
    setup = signal.engine if request.strategy == ENGINE else signal.plays.get(request.strategy)
    if setup is None or setup.confidence < request.min_confidence:
        return None
    if setup.risk_reward + 1e-9 < request.risk_reward:
        return None
    if setup.direction == "SELL" and not prep.shorting:
        return None                         # a spot book: nothing to sell short
    return setup


# ── the order's life ──────────────────────────────────────────

@dataclass
class _Open:
    trade: BacktestTrade
    long: bool
    entry: float
    stop: float
    initial_stop: float
    target: float
    #: What the order was sized against: limit price to stop.
    planned_risk: float
    index: int
    tier: int = 0
    peak_r: float = 0.0
    trough_r: float = 0.0


def _limit_fill(setup: Setup, bar_open: float, bar_high: float,
                bar_low: float) -> Optional[float]:
    # A resting order is filled when the market trades *through* its price.
    # A bar that only touches it says the price was reached, not that an
    # order waiting in the queue there was.
    if setup.direction == "BUY":
        if bar_open <= setup.entry:
            return bar_open                 # gapped through: better than asked
        return setup.entry if bar_low < setup.entry else None
    if bar_open >= setup.entry:
        return bar_open
    return setup.entry if bar_high > setup.entry else None


def _exit(position: _Open, prep: Prepared, i: int,
          filled_inside: bool) -> Optional[Tuple[float, str]]:
    bar_open, high, low = prep.open[i], prep.high[i], prep.low[i]
    # The stop triggers on a touch; the target, a resting limit, needs the
    # market to trade through it (as an entry does).
    if position.long:
        stop_hit, gapped = low <= position.stop, bar_open <= position.stop
        target_hit = high > position.target
    else:
        stop_hit, gapped = high >= position.stop, bar_open >= position.stop
        target_hit = low < position.target
    if stop_hit:                            # pessimistic ordering — stop wins ties
        # Held at the open and the open is already past the stop: it fills
        # where the market is, not where the stop was.
        return (bar_open if gapped and not filled_inside else position.stop), "stop"
    if target_hit and not filled_inside:
        return position.target, "target"
    if prep.intraday and prep.session_last[i]:
        return float(prep.close[i]), "eod"
    if not prep.intraday and i - position.index >= MAX_HOLD_BARS:
        return float(prep.close[i]), "timeout"
    return None


def _ratchet(position: _Open, prep: Prepared, i: int, instrument: Instrument,
             symbol: str, timeframe: str) -> None:
    """Move the stop up the protection ladder — `analysis/risk.py`, the same
    function that moves the real one — at this bar's close."""
    atr = prep.range20[i]
    if np.isnan(atr) or atr <= 0:
        return
    close = float(prep.close[i])
    plan = risk_engine.build_plan(Position(
        id="replay", symbol=symbol, timeframe=timeframe,
        side=PositionSide.LONG if position.long else PositionSide.SHORT,
        entry=position.entry, stop_loss=position.stop,
        initial_stop=position.initial_stop, take_profit=position.target,
        lots=1.0, opened_at=float(prep.time[position.index]),
        protection_tier=position.tier), close, instrument, float(atr))
    if plan is None or not plan.action_available or plan.suggested_stop is None:
        return
    tier = next((t.index for t in plan.tiers if t.active), 0)
    new_stop = plan.suggested_stop
    tighter = (position.stop < new_stop < close) if position.long else (
        close < new_stop < position.stop)
    if tier > position.tier and tighter:
        position.stop, position.tier = new_stop, tier


def _enter(setup: Setup, fill: float, i: int, prep: Prepared,
           instrument: Instrument) -> _Open:
    trade = BacktestTrade(
        index=i, time=int(prep.time[i]), direction=setup.direction,
        entry=round(float(fill), instrument.digits),
        stop_loss=round(setup.stop, instrument.digits),
        take_profit=round(setup.target, instrument.digits),
        confidence=round(setup.confidence, 1), reason=setup.reason)
    return _Open(trade=trade, long=setup.direction == "BUY", entry=float(fill),
                 stop=setup.stop, initial_stop=setup.stop, target=setup.target,
                 planned_risk=abs(setup.entry - setup.stop), index=i)


def _outcome(r_multiple: float) -> str:
    if abs(r_multiple) <= 0.15:
        return "breakeven"
    return "win" if r_multiple > 0 else "loss"


def _settle(position: _Open, prep: Prepared, i: int, exit_price: float, kind: str,
            request: BacktestRequest, instrument: Instrument,
            risk_amount: float) -> BacktestTrade:
    """Record the exit on the trade: net R, what it cost, and why it ended."""
    move = ((exit_price - position.entry) if position.long
            else (position.entry - exit_price))
    cost = 0.0
    if request.include_costs:
        # The entry was a resting limit and so is a target; a stop, a
        # square-off and a time-out all cross the spread.
        cost = costs.round_trip_cost(
            instrument, position.entry, exit_price, stopped=kind == "stop",
            entry_passive=True, exit_passive=kind == "target",
            profile=request.cost_profile,
            seconds_held=float(prep.time[i] - prep.time[position.index]))
    risk = position.planned_risk
    r_multiple = (move - cost) / risk if risk > 0 else 0.0
    trade = position.trade
    trade.exit_price = round(float(exit_price), instrument.digits)
    trade.exit_time = int(prep.time[i])
    trade.exit_reason = kind
    trade.outcome = _outcome(r_multiple)
    trade.r_multiple = round(float(r_multiple), 3)
    trade.cost_r = round(float(cost / risk), 3) if risk > 0 else 0.0
    trade.pnl = round(float(r_multiple * risk_amount), 2)
    trade.bars_held = i - position.index
    return trade


def _simulate(prep: Prepared, instrument: Instrument, request: BacktestRequest,
              start: int, end: int) -> Tuple[List[BacktestTrade], List[Dict[str, float]]]:
    """Trade bars [start, end). Returns (trades, equity_curve)."""
    end = min(end, prep.n)
    rng = np.random.default_rng(request.seed)
    timeout = settings.live_entry_timeout_seconds
    valid_bars = max(1, math.ceil(timeout / prep.tf_seconds)) if timeout else 1
    symbol, timeframe = request.symbol.upper(), request.timeframe.lower()

    balance = request.starting_balance
    equity_curve: List[Dict[str, float]] = [
        {"time": float(prep.time[start]), "equity": balance}]
    trades: List[BacktestTrade] = []
    position: Optional[_Open] = None
    pending: Optional[Tuple[Setup, int]] = None     # (setup, bar it was signalled on)

    for i in range(start, end):
        # ── a resting entry ──────────────────────────────────
        filled_inside = False
        if pending is not None:
            setup, placed = pending
            fill = _limit_fill(setup, prep.open[i], prep.high[i], prep.low[i])
            if fill is not None:
                pending = None
                filled_inside = fill != prep.open[i]
                position = _enter(setup, fill, i, prep, instrument)
                trades.append(position.trade)
            elif i - placed >= valid_bars:
                pending = None              # the market never came back: cancelled

        # ── manage what is open ──────────────────────────────
        if position is not None:
            exit_ = _exit(position, prep, i, filled_inside)
            if exit_ is None:
                if request.use_ladder:
                    _ratchet(position, prep, i, instrument, symbol, timeframe)
                continue
            risk_amount = max(balance, 0.0) * (request.risk_pct / 100.0)
            trade = _settle(position, prep, i, exit_[0], exit_[1], request,
                            instrument, risk_amount)
            balance += trade.pnl
            equity_curve.append({"time": float(prep.time[i]), "equity": round(balance, 2)})
            position = None
            continue

        # ── a new signal at this bar's close ─────────────────
        if pending is None and i + 1 < end and prep.entry_ok[i]:
            setup = _setup_for(request, prep, i, rng)
            if setup is not None:
                pending = (setup, i)

    if position is not None:
        position.trade.outcome = "open"
        position.trade.bars_held = end - 1 - position.index
    return trades, equity_curve


# ── is there an edge ──────────────────────────────────────────

def _closed(trades: List[BacktestTrade]) -> List[BacktestTrade]:
    return [t for t in trades if t.outcome in ("win", "loss", "breakeven")]


def edge_verdict(trades: List[BacktestTrade], method: str, seed: int = 7) -> EdgeVerdict:
    """Positive expectancy after costs, with enough trades that the lower end
    of a 90% bootstrap interval is still above zero. A point estimate above
    zero on thirty trades is what luck looks like."""
    r = np.array([t.r_multiple for t in _closed(trades)], dtype=float)
    verdict = EdgeVerdict(method=method, trades=int(len(r)))
    if len(r) == 0:
        verdict.reason = "No out-of-sample trades — nothing was measured."
        return verdict
    rng = np.random.default_rng(seed)
    means = rng.choice(r, size=(10_000, len(r)), replace=True).mean(axis=1)
    verdict.expectancy = round(float(r.mean()), 3)
    verdict.ci_low = round(float(np.percentile(means, 5)), 3)
    verdict.ci_high = round(float(np.percentile(means, 95)), 3)
    if len(r) < MIN_EDGE_TRADES:
        # No p-value either: resampling three winners "never" shows a loss,
        # and that is a statement about three trades, not about the market.
        verdict.reason = ("Only {} out-of-sample trades (need {}) — too few to tell "
                          "an edge from luck.".format(len(r), MIN_EDGE_TRADES))
        return verdict
    # How often resampling these very trades gives no edge at all. Floored at
    # one in the number of resamples: "never happened" is not "impossible".
    verdict.p_value = round(float(max((means <= 0).mean(), 1.0 / len(means))), 5)
    if verdict.ci_low <= 0:
        verdict.reason = ("No measured edge: out-of-sample expectancy after costs is "
                          "{:+.3f}R, and its 90% interval ({:+.3f}R to {:+.3f}R) "
                          "includes zero.".format(verdict.expectancy, verdict.ci_low,
                                                  verdict.ci_high))
    else:
        verdict.passed = True
        verdict.reason = ("Out-of-sample expectancy after costs is {:+.3f}R over {} "
                          "trades (90% interval {:+.3f}R to {:+.3f}R).".format(
                              verdict.expectancy, len(r), verdict.ci_low, verdict.ci_high))
    return verdict


# ── loading a market ──────────────────────────────────────────

async def _frame(request: BacktestRequest) -> Optional[pd.DataFrame]:
    symbol, timeframe = request.symbol.upper(), request.timeframe.lower()
    # Extra history in front of the tested bars, so the first of them is
    # analysed with as deep a window as the live engine has.
    wanted = request.bars + replay.ANALYSIS_BARS
    await feed.ensure_history(symbol, timeframe, wanted)
    return store.frame(symbol, timeframe, wanted)


SIMULATED = "synthetic"
NOT_EVIDENCE = ("Measured on simulated bars — a test of the software, not of a "
                "market. Connect the venue that serves {} and run it again.")


def _data_source(request: BacktestRequest) -> str:
    """Where the bars under this test came from."""
    return feed.source_of(request.symbol, request.timeframe) or "unknown"


def _not_evidence(result: BacktestResult) -> None:
    """A verdict reached on simulated bars is withdrawn, whatever it was."""
    if result.data_source != SIMULATED:
        return
    reason = NOT_EVIDENCE.format(result.symbol)
    result.edge = result.edge.model_copy(update={"passed": False, "reason": reason})
    result.next_parameters = {}
    result.notes.insert(0, "SIMULATED DATA. " + reason)


async def _higher_frames(request: BacktestRequest) -> Dict[str, pd.DataFrame]:
    """Stored history for the timeframes above the chart's, for the engine's
    multi-timeframe read. Missing ones are rebuilt from the chart's own bars."""
    symbol = request.symbol.upper()
    base = get_timeframe(request.timeframe.lower()).seconds
    frames: Dict[str, pd.DataFrame] = {}
    for tf in MTF_LADDER:
        if get_timeframe(tf).seconds <= base:
            continue
        try:
            await feed.ensure_history(symbol, tf)
        except Exception as exc:  # noqa: BLE001
            log.debug("history for %s %s unavailable: %s", symbol, tf, exc)
        if feed.source_of(symbol, tf) != feed.source_of(symbol, request.timeframe):
            continue    # another source's bars: rebuilt from the chart's own instead
        frame = store.frame(symbol, tf, 6000)
        if frame is not None and len(frame) >= replay.MIN_BARS:
            frames[tf] = frame
    return frames


def _first_tested(n: int, bars: int) -> int:
    return max(WARMUP, n - bars)


async def _prepare(request: BacktestRequest, df: pd.DataFrame) -> Prepared:
    symbol, timeframe = request.symbol.upper(), request.timeframe.lower()
    instrument = get_instrument(symbol)
    if request.strategy == RANDOM:
        return prepare(df, instrument, timeframe, cost_profile=request.cost_profile)
    higher = await _higher_frames(request)
    start = _first_tested(len(df), request.bars)
    # A pass over a few thousand bars is tens of seconds of pandas. Off the
    # event loop, or every chart and price tick waits for it.
    signals = await asyncio.to_thread(
        replay.signals, symbol, timeframe, df, higher, instrument,
        range(start, len(df)), balance=request.starting_balance,
        risk_pct=request.risk_pct)
    return prepare(df, instrument, timeframe, signals, request.cost_profile)


def _empty(request: BacktestRequest) -> BacktestResult:
    return BacktestResult(
        symbol=request.symbol.upper(), timeframe=request.timeframe.lower(),
        strategy=request.strategy, bars_tested=0, from_time=0, to_time=0,
        metrics=metrics_mod.BacktestMetrics(ending_balance=request.starting_balance))


def _unknown_strategy(request: BacktestRequest, result: BacktestResult) -> bool:
    if request.strategy in STRATEGIES:
        return False
    result.notes.append("Unknown strategy '{}'. Choose one of: {}.".format(
        request.strategy, ", ".join(STRATEGIES)))
    result.edge = EdgeVerdict(reason="Unknown strategy.")
    return True


def _venue_notes(request: BacktestRequest, instrument: Instrument) -> List[str]:
    """What this run was charged as, and what that venue does not allow."""
    assumed = costs.describe(instrument, request.cost_profile)
    notes = ["Charged as {}: {:.3f}% a side taken, {:.3f}% resting ({}).".format(
        assumed["profile"], 100 * assumed["taker_per_side"],
        100 * assumed["maker_per_side"], assumed["source"])]
    if not costs.can_short(instrument, request.cost_profile):
        notes.append("Long only: this is a spot book, and a short cannot be opened "
                     "on one. SELL signals were not traded.")
    return notes


def _replay_notes(request: BacktestRequest) -> List[str]:
    if request.strategy == RANDOM:
        return ["Random entries: the control. It has no edge by construction, so "
                "after costs it should lose — if it does not, distrust everything "
                "else this harness reports."]
    notes = [
        "Signals are the live engine's own, replayed bar by bar with the "
        "deterministic analyst. Not reproduced: Claude's verdict, your drawings, "
        "and timeframes below the chart's in the multi-timeframe read."]
    if request.use_ladder:
        notes.append("The protection ladder moves the stop at bar closes; live it "
                     "moves within the bar.")
    return notes


# ── a single run ──────────────────────────────────────────────

async def run_backtest(request: BacktestRequest) -> BacktestResult:
    result = _empty(request)
    if _unknown_strategy(request, result):
        return result
    instrument = get_instrument(request.symbol.upper())
    df = await _frame(request)
    result.data_source = _data_source(request)
    if df is None or len(df) < WARMUP + 50:
        result.notes.append(
            "Not enough history to backtest ({} bars, need {})".format(
                0 if df is None else len(df), WARMUP + 50))
        return result

    started = time.perf_counter()
    prep = await _prepare(request, df)
    n = prep.n
    first = _first_tested(n, request.bars)
    trades, equity_curve = _simulate(prep, instrument, request, first, n)

    result.bars_tested = n - first
    result.from_time = int(prep.time[first])
    result.to_time = int(prep.time[-1])
    result.trades = trades[-400:]
    result.equity_curve = equity_curve[-1000:]
    result.metrics = metrics_mod.compute(
        trades, request.starting_balance, equity_curve, result.bars_tested)

    # ── the same run, split in time ──────────────────────────
    split = first + int((n - first) * (1.0 - min(max(request.oos_fraction, 0.1), 0.6)))
    early = [t for t in trades if t.index < split]
    late = [t for t in trades if t.index >= split]
    result.in_sample = metrics_mod.compute(early, request.starting_balance, [], split - first)
    result.out_of_sample = metrics_mod.compute(late, request.starting_balance, [], n - split)
    result.edge = edge_verdict(late, "holdout", request.seed)

    result.notes.append(
        "Replayed {} bars in {:.1f}s. An entry is a limit at the signal price that "
        "is cancelled if the next bar does not trade through it; when a bar spans "
        "both stop and target the stop is assumed to fill first.".format(
            result.bars_tested, time.perf_counter() - started))
    result.notes += _replay_notes(request)
    result.notes += _venue_notes(request, instrument)
    if prep.intraday:
        result.notes.append(
            "Traded intraday, as live orders are: open positions are squared off at "
            "each session's last bar and no entry is taken close to it.")
    if request.include_costs:
        charged = [t.cost_r for t in _closed(trades)]
        result.notes.append(
            "Net of costs: spread, brokerage, statutory charges and stop slippage "
            "averaged {:.3f}R per trade.".format(float(np.mean(charged)) if charged else 0.0))
    else:
        result.notes.append("COSTS ARE OFF — these figures are not achievable.")
    result.notes.append(
        "The last {:.0f}% of bars are reported separately as out-of-sample. That is "
        "only a fair test if these parameters were not chosen by looking at it — "
        "use walk-forward for that.".format(100 * (n - split) / max(n - first, 1)))
    if len(trades) < 25:
        result.notes.append(
            "Only {} trades — too small a sample to draw conclusions from. Widen the "
            "window or lower the confidence threshold.".format(len(trades)))
    _not_evidence(result)
    return result


# ── walk-forward ──────────────────────────────────────────────

#: The small grid walk-forward is allowed to choose from. Kept small on
#: purpose: every extra combination is another chance to fit noise.
WF_GRID = [(conf, rr) for conf in (50.0, 60.0, 70.0) for rr in (1.5, 2.0, 3.0)]
#: A parameter set needs this many trades on a training stretch to be scored.
WF_MIN_TRAIN_TRADES = 8


def _best_parameters(prep: Prepared, instrument: Instrument, request: BacktestRequest,
                     start: int, end: int) -> Tuple[Optional[Tuple[float, float]], float]:
    """The grid point with the best expectancy on bars [start, end) — or
    None when none of them made money there.

    "The least bad" is not a reason to trade. If nothing worked on the
    stretch just gone, the stretch that follows is sat out, and that
    sitting-out is part of what gets measured.
    """
    best, best_score = None, 0.0
    for conf, rr in WF_GRID:
        candidate = request.model_copy(update={"min_confidence": conf, "risk_reward": rr})
        fitted, _ = _simulate(prep, instrument, candidate, start, end)
        r = [t.r_multiple for t in _closed(fitted)]
        if len(r) >= WF_MIN_TRAIN_TRADES and float(np.mean(r)) > best_score:
            best, best_score = (conf, rr), float(np.mean(r))
    return best, best_score


def _trade_fold(prep: Prepared, instrument: Instrument, request: BacktestRequest,
                fold: int, train: Tuple[int, int], test: Tuple[int, int]):
    """Fit on `train`, trade `test`. Returns (parameters, trades, summary)."""
    chosen, score = _best_parameters(prep, instrument, request, train[0], train[1])
    trades: List[BacktestTrade] = []
    if chosen is not None:
        trades, _ = _simulate(prep, instrument, request.model_copy(update={
            "min_confidence": chosen[0], "risk_reward": chosen[1]}), test[0], test[1])
    done = [t.r_multiple for t in _closed(trades)]
    summary = {
        "fold": fold,
        "min_confidence": chosen[0] if chosen else 0.0,
        "risk_reward": chosen[1] if chosen else 0.0,
        "train_expectancy": round(score, 3) if chosen else 0.0,
        "test_trades": len(done),
        "test_expectancy": round(float(np.mean(done)), 3) if done else 0.0,
    }
    return chosen, trades, summary


async def walk_forward(request: BacktestRequest, folds: int = 4) -> BacktestResult:
    """Choose parameters on one stretch of history, trade them on the next,
    roll forward, repeat. Only the traded stretches are reported, so nothing
    in the result was ever fitted to the bars it was measured on."""
    result = _empty(request)
    if _unknown_strategy(request, result):
        return result
    instrument = get_instrument(request.symbol.upper())
    df = await _frame(request)
    result.data_source = _data_source(request)

    first = _first_tested(0 if df is None else len(df), request.bars)
    usable = 0 if df is None else len(df) - first
    if df is None or usable < (folds + 1) * 100:
        result.edge = EdgeVerdict(method="walk_forward",
                                  reason="Not enough history for walk-forward.")
        result.notes.append("Not enough history for a {}-fold walk-forward ({} usable "
                            "bars, need {}).".format(folds, max(usable, 0), (folds + 1) * 100))
        return result

    started = time.perf_counter()
    prep = await _prepare(request, df)
    n = prep.n
    step = usable // (folds + 1)
    traded: List[BacktestTrade] = []
    for fold in range(folds):
        train_start = first + fold * step
        test_start = train_start + step
        test_end = n if fold == folds - 1 else test_start + step
        _, trades, summary = _trade_fold(
            prep, instrument, request, fold + 1,
            (train_start, test_start), (test_start, test_end))
        traded += trades
        result.folds.append(summary)

    tested_from = first + step
    result.bars_tested = n - tested_from
    result.from_time, result.to_time = int(prep.time[tested_from]), int(prep.time[-1])
    result.trades = traded[-400:]
    result.metrics = metrics_mod.compute(traded, request.starting_balance, [], result.bars_tested)
    result.out_of_sample = result.metrics
    result.edge = edge_verdict(traded, "walk_forward", request.seed)

    # What to trade next: fitted on the most recent stretch, exactly as each
    # fold above was fitted on the stretch before it. A live order has to
    # clear these to count as the kind of trade that was tested — and if
    # nothing made money on the most recent stretch there are none, which is
    # the same "sit this one out" the folds obeyed.
    upcoming, _ = _best_parameters(prep, instrument, request, n - step, n)
    if upcoming is not None:
        result.next_parameters = {"min_confidence": upcoming[0],
                                  "min_risk_reward": upcoming[1]}
    else:
        result.notes.append(
            "Nothing in the grid made money on the most recent stretch, so there "
            "are no thresholds to trade with right now — whatever the verdict "
            "above says about the past.")

    result.notes.append(
        "{}-fold walk-forward in {:.1f}s: parameters chosen from a {}-combination grid "
        "on each training stretch, then traded on the stretch after it. Every figure "
        "here is out-of-sample and net of costs.".format(
            folds, time.perf_counter() - started, len(WF_GRID)))
    result.notes += _replay_notes(request)
    result.notes += _venue_notes(request, instrument)
    _not_evidence(result)
    return result
