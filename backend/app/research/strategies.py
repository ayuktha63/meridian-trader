"""Candidate strategies — hypotheses to be tested, not improvements.

Each one states what it believes about the market, when that should hold,
when it should fail, and what result would show it to be wrong. Each has a
small grid of parameters declared here, before any of them is run: every
combination tried is another chance to fit noise, and the count goes into
the statistics that judge the winner.

A strategy is a pure function of the bars it is given. The signal at bar
`i` uses nothing after bar `i`; `audit.py` checks that by cutting the
history off at `i` and asking again.

Nothing here is used by the live engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from ..analysis import indicators as ind
from .simulator import Signal

Params = Mapping[str, float]


@dataclass
class Plan:
    """What a strategy would do on a history: an entry decision per bar, and
    (for those that exit on a signal) whether it wants out at each close."""
    signals: Dict[int, Signal] = field(default_factory=dict)
    exit_long: Optional[List[bool]] = None
    exit_short: Optional[List[bool]] = None


@dataclass(frozen=True)
class Strategy:
    name: str
    hypothesis: str
    works_when: str
    fails_when: str
    falsified_by: str
    entry_rule: str
    exit_rule: str
    grid: Tuple[Dict[str, float], ...]
    build: Callable[[pd.DataFrame, Params], Plan]
    #: Bars of history a signal needs before it is the signal it would be
    #: with all the history there is.
    warmup: Callable[[Params], int]


def _atr(df: pd.DataFrame) -> np.ndarray:
    return ind.atr(df, 14).to_numpy(dtype=float)


def _usable(values: np.ndarray) -> np.ndarray:
    return np.isfinite(values) & (values > 0)


def _grid(**axes: Tuple[float, ...]) -> Tuple[Dict[str, float], ...]:
    combos: List[Dict[str, float]] = [{}]
    for name, values in axes.items():
        combos = [dict(combo, **{name: value}) for combo in combos for value in values]
    return tuple(combos)


def label(params: Params) -> str:
    return ",".join("{}={:g}".format(key, params[key]) for key in sorted(params))


# ── trend following: a new N-bar extreme, an exit that trails ─────────

def _donchian(df: pd.DataFrame, params: Params) -> Plan:
    n, k = int(params["n"]), float(params["k"])
    close = df["close"].to_numpy(dtype=float)
    upper = df["high"].rolling(n).max().shift(1).to_numpy(dtype=float)
    lower = df["low"].rolling(n).min().shift(1).to_numpy(dtype=float)
    atr = _atr(df)
    ready = _usable(atr) & np.isfinite(upper) & np.isfinite(lower)
    ready[:n + 100] = False
    plan = Plan()
    for i in np.flatnonzero(ready & (close > upper)):
        plan.signals[int(i)] = Signal(1, close[i] - k * atr[i], trail_atr=k, tag="donchian")
    for i in np.flatnonzero(ready & (close < lower)):
        plan.signals[int(i)] = Signal(-1, close[i] + k * atr[i], trail_atr=k, tag="donchian")
    return plan


DONCHIAN = Strategy(
    name="donchian_trend",
    hypothesis="Prices trend: a close beyond the extreme of the last N bars marks a move "
               "that continues further than it reverses.",
    works_when="Sustained directional markets — the large bull and bear legs.",
    fails_when="Ranges, where every new extreme is the edge of the range and reverses.",
    falsified_by="Net expectancy not above zero on unseen data, or no better than entries "
                 "at random bars with the same stops and holding times.",
    entry_rule="Close above the highest high (below the lowest low) of the previous N "
               "bars: enter at the next open.",
    exit_rule="Initial stop k ATRs from the entry; a stop trailing k ATRs behind the best "
              "price since entry. No target.",
    grid=_grid(n=(20.0, 55.0, 100.0), k=(2.0, 3.0, 4.0)),
    build=_donchian,
    warmup=lambda p: int(p["n"]) + 100,
)


# ── time-series momentum: the sign of the past return ─────────────────

def _momentum(df: pd.DataFrame, params: Params) -> Plan:
    lookback, k = int(params["lookback"]), float(params["k"])
    close_s = df["close"]
    close = close_s.to_numpy(dtype=float)
    past = (close_s / close_s.shift(lookback) - 1.0).to_numpy(dtype=float)
    before = np.r_[np.nan, past[:-1]]
    atr = _atr(df)
    ready = _usable(atr) & np.isfinite(past) & np.isfinite(before)
    ready[:lookback + 100] = False
    plan = Plan(exit_long=(~(past > 0)).tolist(), exit_short=(~(past < 0)).tolist())
    for i in np.flatnonzero(ready & (past > 0) & (before <= 0)):
        plan.signals[int(i)] = Signal(1, close[i] - k * atr[i], tag="momentum")
    for i in np.flatnonzero(ready & (past < 0) & (before >= 0)):
        plan.signals[int(i)] = Signal(-1, close[i] + k * atr[i], tag="momentum")
    return plan


MOMENTUM = Strategy(
    name="ts_momentum",
    hypothesis="An asset's own past return predicts the sign of its next one: what has "
               "risen over the lookback keeps rising.",
    works_when="Slow, persistent trends; flows that take weeks to complete.",
    fails_when="Sharp reversals and V-shaped markets, where the past return points the "
               "wrong way exactly when it is largest.",
    falsified_by="Net expectancy not above zero on unseen data, or no better than random "
                 "entries held as long.",
    entry_rule="The return over the lookback turns positive (negative): enter long "
               "(short) at the next open.",
    exit_rule="Leave at the next open once the lookback return is no longer on the "
              "trade's side; a stop k ATRs away for the case it never gets the chance.",
    grid=_grid(lookback=(24.0, 72.0, 168.0, 336.0), k=(3.0, 5.0)),
    build=_momentum,
    warmup=lambda p: int(p["lookback"]) + 100,
)


# ── mean reversion, in a market that is not trending ──────────────────

def _reversion(df: pd.DataFrame, params: Params) -> Plan:
    mult, k, adx_max = float(params["mult"]), float(params["k"]), float(params["adx_max"])
    close = df["close"].to_numpy(dtype=float)
    bands = ind.bollinger(df["close"], 20, mult)
    upper, mid, lower = (bands[c].to_numpy(dtype=float) for c in ("upper", "mid", "lower"))
    adx = ind.adx(df, 14)["adx"].to_numpy(dtype=float)
    atr = _atr(df)
    ready = _usable(atr) & np.isfinite(adx) & np.isfinite(mid) & (adx < adx_max)
    ready[:150] = False
    plan = Plan()
    for i in np.flatnonzero(ready & (close < lower)):
        plan.signals[int(i)] = Signal(1, close[i] - k * atr[i], target=float(mid[i]),
                                      max_hold=24, tag="reversion")
    for i in np.flatnonzero(ready & (close > upper)):
        plan.signals[int(i)] = Signal(-1, close[i] + k * atr[i], target=float(mid[i]),
                                      max_hold=24, tag="reversion")
    return plan


REVERSION = Strategy(
    name="range_reversion",
    hypothesis="When a market is not trending, a close outside its Bollinger band is an "
               "overshoot that returns to the mean.",
    works_when="Quiet, range-bound markets (low ADX).",
    fails_when="The break that starts a trend looks, on the first bar, exactly like an "
               "overshoot — and it does not come back.",
    falsified_by="Net expectancy not above zero on unseen data; in particular a win rate "
                 "that does not pay for the stops.",
    entry_rule="ADX(14) below the ceiling and a close below the lower band (above the "
               "upper): enter at the next open, against the move.",
    exit_rule="Target the band's mid-line as it stood at the signal; stop k ATRs away; "
              "out at the close after 24 bars.",
    grid=_grid(mult=(2.0, 2.5), k=(1.5, 2.5), adx_max=(20.0, 25.0)),
    build=_reversion,
    warmup=lambda p: 150,
)


# ── a dip inside an established trend ────────────────────────────────

def _pullback(df: pd.DataFrame, params: Params) -> Plan:
    rsi_max, k = float(params["rsi_max"]), float(params["k"])
    close_s = df["close"]
    close = close_s.to_numpy(dtype=float)
    fast = ind.ema(close_s, 50).to_numpy(dtype=float)
    slow = ind.ema(close_s, 200).to_numpy(dtype=float)
    rsi = ind.rsi(close_s, 3).to_numpy(dtype=float)
    recent_high = df["high"].rolling(20).max().to_numpy(dtype=float)
    recent_low = df["low"].rolling(20).min().to_numpy(dtype=float)
    atr = _atr(df)
    ready = _usable(atr) & np.isfinite(slow) & np.isfinite(rsi) & np.isfinite(recent_high)
    ready[:800] = False
    up = ready & (fast > slow) & (close > slow) & (rsi < rsi_max)
    down = ready & (fast < slow) & (close < slow) & (rsi > 100.0 - rsi_max)
    plan = Plan()
    for i in np.flatnonzero(up & (recent_high - close > 0.5 * atr)):
        plan.signals[int(i)] = Signal(1, close[i] - k * atr[i], target=float(recent_high[i]),
                                      max_hold=48, tag="pullback")
    for i in np.flatnonzero(down & (close - recent_low > 0.5 * atr)):
        plan.signals[int(i)] = Signal(-1, close[i] + k * atr[i], target=float(recent_low[i]),
                                      max_hold=48, tag="pullback")
    return plan


PULLBACK = Strategy(
    name="trend_pullback",
    hypothesis="In an established trend, a short sharp move against it is bought back: "
               "the trend resumes to at least its recent extreme.",
    works_when="Orderly trends with shallow corrections.",
    fails_when="The correction is the start of the reversal; the trend filter lags it.",
    falsified_by="Net expectancy not above zero on unseen data, or no better than random "
                 "entries taken in the same trend direction.",
    entry_rule="EMA50 above EMA200 with price above both, and RSI(3) below the "
               "threshold (mirrored for a downtrend): enter at the next open.",
    exit_rule="Target the 20-bar extreme as it stood at the signal; stop k ATRs away; "
              "out at the close after 48 bars.",
    grid=_grid(rsi_max=(15.0, 25.0), k=(1.5, 2.5)),
    build=_pullback,
    warmup=lambda p: 800,
)


# ── expansion out of a compressed range ──────────────────────────────

def _expansion(df: pd.DataFrame, params: Params) -> Plan:
    pct, k = float(params["pct"]), float(params["k"])
    close = df["close"].to_numpy(dtype=float)
    width = ind.bollinger(df["close"], 20, 2.0)["width"]
    rank = width.rolling(200).rank(pct=True)
    squeezed = (rank.shift(1) <= pct).to_numpy()
    upper = df["high"].rolling(20).max().shift(1).to_numpy(dtype=float)
    lower = df["low"].rolling(20).min().shift(1).to_numpy(dtype=float)
    atr = _atr(df)
    ready = _usable(atr) & np.isfinite(upper) & squeezed
    ready[:320] = False
    plan = Plan()
    for i in np.flatnonzero(ready & (close > upper)):
        plan.signals[int(i)] = Signal(1, close[i] - k * atr[i], trail_atr=k, tag="expansion")
    for i in np.flatnonzero(ready & (close < lower)):
        plan.signals[int(i)] = Signal(-1, close[i] + k * atr[i], trail_atr=k, tag="expansion")
    return plan


EXPANSION = Strategy(
    name="vol_expansion",
    hypothesis="Volatility clusters and cycles: a break out of an unusually narrow range "
               "starts a move larger than the range that preceded it.",
    works_when="After a quiet consolidation, when the break has participation behind it.",
    fails_when="False breaks — the range was narrow because nobody cared, and still nobody does.",
    falsified_by="Net expectancy not above zero on unseen data, or no better than the "
                 "plain N-bar breakout it is a filtered version of.",
    entry_rule="Band width in the lowest share of its last 200 bars on the previous bar, "
               "and a close beyond the previous 20-bar extreme: enter at the next open.",
    exit_rule="Initial stop k ATRs from the entry; trailing stop k ATRs behind the best "
              "price. No target.",
    grid=_grid(pct=(0.15, 0.30), k=(2.0, 3.0)),
    build=_expansion,
    warmup=lambda p: 320,
)


CANDIDATES: Tuple[Strategy, ...] = (DONCHIAN, MOMENTUM, REVERSION, PULLBACK, EXPANSION)
BY_NAME: Dict[str, Strategy] = {s.name: s for s in CANDIDATES}


def trials() -> int:
    """Parameter combinations declared across every candidate."""
    return sum(len(s.grid) for s in CANDIDATES)
