"""Intraday candidates for NSE cash equities on 15-minute bars.

Each is a hypothesis with a grid declared here, before any of it is run
(`india_protocol.json` freezes the same list and its hash goes into every
experiment record). Each is a pure function of the bars it is given: the
decision at bar `i` uses nothing after bar `i`, which `audit.py` checks by
cutting the history off at `i` and asking again.

Common contract (see also each `Strategy`'s own text):

* **Input** — a frame of one stock's 15-minute bars, columns time, open, high,
  low, close, volume; time is the UTC epoch of the bar's open.
* **Decision** — at a bar's close. The simulator acts at the next bar's open,
  as a market order.
* **Session** — every position is flat by the close of the 15:00 bar (15:15),
  five minutes ahead of the live engine's own square-off line; no entry is
  taken after the cut-off in `LAST_ENTRY`. A trade therefore never spans two
  sessions, and is charged as an intraday order.
* **Daily context** is the previous *completed* day's values only. Today's
  open, high and low are used only as they stand at the decision bar.
* **Invalid data** — a bar with no usable ATR, or a day whose range cannot be
  measured, produces no signal. Nothing is filled forward.
* **Costs** are not charged here. The simulator is run without them and the
  trades are priced order by order (`india_costing`).

A signal is not an order: nothing here knows of a broker or an account.

Not built, and said so: cross-sectional momentum (needs a ranking across
stocks, which this per-stock interface cannot express) and daily mean
reversion. They are in the protocol's register as not completed.
"""
from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from ..analysis import indicators as ind
from .exchange_calendar import NSE_EQUITY
from .simulator import Signal
from .strategies import Params, Plan, Strategy, _grid

IST_OFFSET = 19_800
#: Seconds after local midnight.
OPEN = 9 * 3600 + 15 * 60
FLAT_BY = 15 * 3600                 # a position is closed at or before this
LAST_ENTRY = 13 * 3600 + 30 * 60    # no entry decided after this
TF = 900

VERSION = "1"


class Day:
    """What the clock says about each bar, and what the day before it said.
    Everything here is known at the bar's close."""

    def __init__(self, df: pd.DataFrame) -> None:
        t = df["time"].to_numpy(dtype=float)
        n = len(t)
        self.n = n
        day = NSE_EQUITY.local_day(t)
        fresh = np.r_[True, day[1:] != day[:-1]] if n else np.zeros(0, dtype=bool)
        starts = np.flatnonzero(fresh)
        self.id = np.cumsum(fresh) - 1
        self.start = starts[self.id] if n else starts
        self.pos = np.arange(n) - self.start
        self.sod = np.mod(t + IST_OFFSET, 86_400)
        #: Bars that can still be held after the entry bar and stay inside
        #: the session: the exit bar closes by `FLAT_BY`.
        self.hold = np.floor((FLAT_BY - self.sod - 2 * TF) / TF).astype(int)
        close = df["close"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        days = int(self.id[-1]) + 1 if n else 0
        grouped = pd.DataFrame({"d": self.id, "c": close, "h": high, "l": low})
        daily = grouped.groupby("d").agg(c=("c", "last"), h=("h", "max"), l=("l", "min"))

        def previous(values: np.ndarray) -> np.ndarray:
            shifted = np.r_[np.nan, values[:-1]] if days else values
            return shifted[self.id]
        self.prev_close = previous(daily["c"].to_numpy())
        self.prev_high = previous(daily["h"].to_numpy())
        self.prev_low = previous(daily["l"].to_numpy())
        self.daily_close = daily["c"]
        self.open_of_day = df["open"].to_numpy(dtype=float)[self.start] if n else np.zeros(0)

    def prev_daily_ema(self, length: int) -> np.ndarray:
        """EMA of completed daily closes, as it stood before today."""
        ema = self.daily_close.ewm(span=length, adjust=False).mean().to_numpy()
        shifted = np.r_[np.nan, ema[:-1]] if len(ema) else ema
        return shifted[self.id]

    def can_enter(self, i: int) -> bool:
        return bool(self.hold[i] >= 0 and self.sod[i] <= LAST_ENTRY)


def _bars(df: pd.DataFrame) -> Tuple[np.ndarray, ...]:
    return tuple(df[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))


def _atr(df: pd.DataFrame) -> np.ndarray:
    return ind.atr(df, 14).to_numpy(dtype=float)


def _sane(atr: float, risk: float, low: float = 0.3, high: float = 4.0) -> bool:
    return atr > 0 and np.isfinite(risk) and low * atr <= risk <= high * atr


def _signal(direction: int, stop: float, *, target: Optional[float] = None,
            trail: Optional[float] = None, hold: int, tag: str) -> Signal:
    return Signal(direction, float(stop), target=None if target is None else float(target),
                  trail_atr=trail, max_hold=max(int(hold), 0), tag=tag)


# ── A. opening range breakout ─────────────────────────────────

def _orb(df: pd.DataFrame, params: Params) -> Plan:
    bars, rr = int(params["or_bars"]), float(params["rr"])
    _, high, low, close = _bars(df)
    atr, day, plan = _atr(df), Day(df), Plan()
    for first in np.unique(day.start):
        last = first + bars
        if last >= day.n or day.id[last] != day.id[first]:
            continue
        top, bottom = high[first:last].max(), low[first:last].min()
        j = last
        while j < day.n and day.id[j] == day.id[first]:
            if day.can_enter(j) and np.isfinite(atr[j]):
                if close[j] > top and _sane(atr[j], close[j] - bottom):
                    plan.signals[j] = _signal(1, bottom, target=close[j] + rr * (close[j] - bottom),
                                              hold=day.hold[j], tag="orb")
                    break
                if close[j] < bottom and _sane(atr[j], top - close[j]):
                    plan.signals[j] = _signal(-1, top, target=close[j] - rr * (top - close[j]),
                                              hold=day.hold[j], tag="orb")
                    break
                if close[j] > top or close[j] < bottom:
                    break               # a break the range is too wide or narrow to trade
            j += 1
    return plan


ORB = Strategy(
    name="orb15",
    hypothesis="The first minutes of the session set a range; a close beyond it, "
               "the first of the day, starts a move that carries.",
    works_when="Days with a directional open — news, gaps, a trending index.",
    fails_when="Range-bound days, where the first break is the extreme and reverses.",
    falsified_by="Net expectancy not above zero after costs, or no better than entries at "
                 "random bars with the same stops, targets and holding times.",
    entry_rule="The first bar after the opening range (`or_bars` bars from 09:15) to close "
               "above its high (below its low), before 13:30: enter at the next open.",
    exit_rule="Stop at the far side of the range; target `rr` times that risk; out at "
              "the close of the 15:00 bar. One trade a day.",
    grid=_grid(or_bars=(2.0, 4.0), rr=(1.5, 2.5)),
    build=_orb,
    warmup=lambda p: 400,
)


# ── B/K. trend with a daily filter, and the same with a volatility filter ──

def _trend(df: pd.DataFrame, params: Params) -> Plan:
    n, k = int(params["n"]), float(params["k"])
    vol_floor = float(params.get("vol_pct", 0.0))
    _, high, low, close = _bars(df)
    atr, day, plan = _atr(df), Day(df), Plan()
    ceiling = df["high"].rolling(n).max().shift(1).to_numpy(dtype=float)
    floor = df["low"].rolling(n).min().shift(1).to_numpy(dtype=float)
    ema = day.prev_daily_ema(20)
    rank = np.ones(day.n)
    if vol_floor > 0:
        rank = (pd.Series(atr / close).rolling(500).rank(pct=True)).to_numpy(dtype=float)
    for i in range(day.n):
        if not day.can_enter(i) or day.pos[i] < 2 or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if not np.isfinite(ema[i]) or not np.isfinite(ceiling[i]):
            continue
        if vol_floor > 0 and not rank[i] >= vol_floor:
            continue
        if close[i] > ceiling[i] and day.prev_close[i] > ema[i]:
            plan.signals[i] = _signal(1, close[i] - k * atr[i], trail=k, hold=day.hold[i],
                                      tag="trend15")
        elif close[i] < floor[i] and day.prev_close[i] < ema[i]:
            plan.signals[i] = _signal(-1, close[i] + k * atr[i], trail=k, hold=day.hold[i],
                                      tag="trend15")
    return plan


TREND = Strategy(
    name="trend15",
    hypothesis="With the daily trend, a break of the last N bars' extreme continues "
               "further than it reverses.",
    works_when="Trending days in a trending market.",
    fails_when="Chop, and the reversal day against yesterday's trend.",
    falsified_by="Net expectancy not above zero after costs, or no better than random "
                 "entries taken in the same direction.",
    entry_rule="Close beyond the previous N bars' high (low), with yesterday's close above "
               "(below) the EMA(20) of daily closes: enter at the next open.",
    exit_rule="Stop k ATRs away, trailing k ATRs behind the best price; out at 15:15.",
    grid=_grid(n=(16.0, 32.0), k=(1.5, 2.5), vol_pct=(0.0,)),
    build=_trend,
    warmup=lambda p: 2500,
)

TREND_VOL = Strategy(
    name="trend15_vol",
    hypothesis="The same trend rule, taken only when volatility is above its own median "
               "of the last 500 bars: a predeclared filter, tested beside the unfiltered "
               "rule rather than chosen from it.",
    works_when="Volatile trending days.",
    fails_when="The filter removes the trades that paid.",
    falsified_by="Not better than `trend15` at the same N and k on the same stretch.",
    entry_rule=TREND.entry_rule + " ATR/close must rank at or above the median of the "
               "last 500 bars.",
    exit_rule=TREND.exit_rule,
    grid=_grid(n=(16.0, 32.0), k=(1.5, 2.5), vol_pct=(0.5,)),
    build=_trend,
    warmup=lambda p: 2500,
)


# ── C. supertrend ─────────────────────────────────────────────

def _supertrend(df: pd.DataFrame, params: Params) -> Plan:
    mult, adx_min = float(params["mult"]), float(params["adx_min"])
    _, _, _, close = _bars(df)
    atr, day, plan = _atr(df), Day(df), Plan()
    st = ind.supertrend(df, 10, mult)
    direction, line = st["direction"].to_numpy(), st["supertrend"].to_numpy(dtype=float)
    adx = ind.adx(df, 14)["adx"].to_numpy(dtype=float) if adx_min > 0 else np.full(day.n, 99.0)
    for i in range(1, day.n):
        if not day.can_enter(i) or day.pos[i] < 1 or direction[i] == direction[i - 1]:
            continue
        if not (np.isfinite(line[i]) and np.isfinite(atr[i])) or not adx[i] >= adx_min:
            continue
        risk = abs(close[i] - line[i])
        if direction[i] == 1 and line[i] < close[i] and _sane(atr[i], risk):
            plan.signals[i] = _signal(1, line[i], hold=day.hold[i], tag="supertrend")
        elif direction[i] == -1 and line[i] > close[i] and _sane(atr[i], risk):
            plan.signals[i] = _signal(-1, line[i], hold=day.hold[i], tag="supertrend")
    return plan


SUPERTREND = Strategy(
    name="supertrend15",
    hypothesis="A close through the Supertrend band (ATR 10) flips the trend and the "
               "new direction persists.",
    works_when="Sustained moves with shallow pullbacks.",
    fails_when="Chop: the band is crossed back and forth.",
    falsified_by="Net expectancy not above zero after costs; or the ADX filter not "
                 "improving it.",
    entry_rule="The Supertrend direction changes at a bar's close (ADX(14) at least "
               "`adx_min`): enter at the next open.",
    exit_rule="Stop at the band's level at the flip; no target; out at 15:15.",
    grid=_grid(mult=(2.0, 3.0), adx_min=(0.0, 20.0)),
    build=_supertrend,
    warmup=lambda p: 1500,
)


# ── D. VWAP reversion ─────────────────────────────────────────

def _vwap(df: pd.DataFrame, params: Params) -> Plan:
    z, adx_max = float(params["z"]), float(params["adx_max"])
    _, _, _, close = _bars(df)
    atr, day, plan = _atr(df), Day(df), Plan()
    line = ind.vwap(df)["vwap"].to_numpy(dtype=float)
    adx = ind.adx(df, 14)["adx"].to_numpy(dtype=float)
    for i in range(day.n):
        if not day.can_enter(i) or day.pos[i] < 4 or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if not (np.isfinite(line[i]) and np.isfinite(adx[i])) or adx[i] > adx_max:
            continue
        distance = (close[i] - line[i]) / atr[i]
        if distance <= -z:
            stop = close[i] - 1.5 * atr[i]
            plan.signals[i] = _signal(1, stop, target=line[i], hold=day.hold[i], tag="vwap")
        elif distance >= z:
            stop = close[i] + 1.5 * atr[i]
            plan.signals[i] = _signal(-1, stop, target=line[i], hold=day.hold[i], tag="vwap")
    return plan


VWAP = Strategy(
    name="vwap_reversion15",
    hypothesis="Away from a trend, price stretched `z` ATRs from the session VWAP "
               "returns to it.",
    works_when="Quiet, two-sided days (low ADX).",
    fails_when="The stretch is the start of a trend day; nothing here assumes every "
               "excursion reverts.",
    falsified_by="Net expectancy not above zero after costs, or no better than random.",
    entry_rule="At least 5 bars into the session, ADX(14) at most `adx_max`, close "
               "`z` ATRs below (above) session VWAP: enter long (short) at the next open.",
    exit_rule="Target the VWAP as it stood at the signal; stop 1.5 ATR beyond entry; "
              "out at 15:15.",
    grid=_grid(z=(1.5, 2.5), adx_max=(25.0, 35.0)),
    build=_vwap,
    warmup=lambda p: 1500,
)


# ── E. Bollinger squeeze breakout ─────────────────────────────

def _squeeze(df: pd.DataFrame, params: Params) -> Plan:
    pct, k = float(params["pct"]), float(params["k"])
    _, _, _, close = _bars(df)
    atr, day, plan = _atr(df), Day(df), Plan()
    width = ind.bollinger(df["close"], 20, 2.0)["width"]
    tight = (width.rolling(200).rank(pct=True).shift(1) <= pct).to_numpy()
    ceiling = df["high"].rolling(20).max().shift(1).to_numpy(dtype=float)
    floor = df["low"].rolling(20).min().shift(1).to_numpy(dtype=float)
    for i in range(day.n):
        if not day.can_enter(i) or day.pos[i] < 2 or not tight[i]:
            continue
        if not (np.isfinite(atr[i]) and atr[i] > 0 and np.isfinite(ceiling[i])):
            continue
        if close[i] > ceiling[i]:
            plan.signals[i] = _signal(1, close[i] - k * atr[i], trail=k, hold=day.hold[i],
                                      tag="squeeze")
        elif close[i] < floor[i]:
            plan.signals[i] = _signal(-1, close[i] + k * atr[i], trail=k, hold=day.hold[i],
                                      tag="squeeze")
    return plan


SQUEEZE = Strategy(
    name="bb_squeeze15",
    hypothesis="A break out of an unusually narrow Bollinger band starts a move larger "
               "than the range before it.",
    works_when="After a quiet stretch, with participation.",
    fails_when="False breaks: the range was narrow because nobody cared.",
    falsified_by="Net expectancy not above zero after costs; not better than `channel15`.",
    entry_rule="Band width in its lowest `pct` of the last 200 bars on the previous bar, "
               "and a close beyond the previous 20-bar extreme: enter at the next open.",
    exit_rule="Stop k ATRs away, trailing; out at 15:15.",
    grid=_grid(pct=(0.15, 0.30), k=(1.5, 2.5)),
    build=_squeeze,
    warmup=lambda p: 1500,
)


# ── F. RSI-confirmed momentum ─────────────────────────────────

def _rsi(df: pd.DataFrame, params: Params) -> Plan:
    level, k = float(params["level"]), float(params["k"])
    _, _, _, close = _bars(df)
    atr, day, plan = _atr(df), Day(df), Plan()
    rsi = ind.rsi(df["close"], 14).to_numpy(dtype=float)
    trend = ind.ema(df["close"], 50).to_numpy(dtype=float)
    for i in range(1, day.n):
        if not day.can_enter(i) or day.pos[i] < 2 or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if not (np.isfinite(rsi[i]) and np.isfinite(rsi[i - 1]) and np.isfinite(trend[i])):
            continue
        if rsi[i - 1] < level <= rsi[i] and close[i] > trend[i]:
            plan.signals[i] = _signal(1, close[i] - k * atr[i], trail=k, hold=day.hold[i],
                                      tag="rsi")
        elif rsi[i - 1] > 100 - level >= rsi[i] and close[i] < trend[i]:
            plan.signals[i] = _signal(-1, close[i] + k * atr[i], trail=k, hold=day.hold[i],
                                      tag="rsi")
    return plan


RSI_MOMENTUM = Strategy(
    name="rsi_momentum15",
    hypothesis="RSI(14) crossing up through a high level, above the 50-bar EMA, is "
               "momentum that persists — not an overbought reading to sell.",
    works_when="Trending sessions.",
    fails_when="Chop, where the cross is the top.",
    falsified_by="Net expectancy not above zero after costs, or no better than random.",
    entry_rule="RSI(14) crosses up through `level` (down through 100 minus it) with the "
               "close above (below) its 50-bar EMA: enter at the next open.",
    exit_rule="Stop k ATRs away, trailing; out at 15:15.",
    grid=_grid(level=(55.0, 60.0), k=(1.5, 2.5)),
    build=_rsi,
    warmup=lambda p: 1500,
)


# ── G. price-channel breakout ─────────────────────────────────

def _channel(df: pd.DataFrame, params: Params) -> Plan:
    n, k = int(params["n"]), float(params["k"])
    _, _, _, close = _bars(df)
    atr, day, plan = _atr(df), Day(df), Plan()
    ceiling = df["high"].rolling(n).max().shift(1).to_numpy(dtype=float)
    floor = df["low"].rolling(n).min().shift(1).to_numpy(dtype=float)
    for i in range(day.n):
        if not day.can_enter(i) or day.pos[i] < 2 or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if not np.isfinite(ceiling[i]):
            continue
        if close[i] > ceiling[i]:
            plan.signals[i] = _signal(1, close[i] - k * atr[i], trail=k, hold=day.hold[i],
                                      tag="channel")
        elif close[i] < floor[i]:
            plan.signals[i] = _signal(-1, close[i] + k * atr[i], trail=k, hold=day.hold[i],
                                      tag="channel")
    return plan


CHANNEL = Strategy(
    name="channel15",
    hypothesis="A close beyond the highest high (lowest low) of the last N bars — the "
               "window shifted so the bar's own extreme is not in it — continues.",
    works_when="Trending days.",
    fails_when="Ranges; every new extreme is the edge of the range.",
    falsified_by="Net expectancy not above zero after costs, or no better than random.",
    entry_rule="Close beyond the previous N bars' extreme: enter at the next open.",
    exit_rule="Stop k ATRs away, trailing; out at 15:15.",
    grid=_grid(n=(20.0, 40.0), k=(1.5, 2.5)),
    build=_channel,
    warmup=lambda p: 1500,
)


# ── H/I. gaps ─────────────────────────────────────────────────

def _gap_state(df: pd.DataFrame, day: Day) -> np.ndarray:
    return day.open_of_day / day.prev_close - 1.0


def _gap_trend(df: pd.DataFrame, params: Params) -> Plan:
    gap_min, rr = float(params["gap"]), float(params["rr"])
    _, high, low, close = _bars(df)
    atr, day, plan = _atr(df), Day(df), Plan()
    gap = _gap_state(df, day)
    opened = day.open_of_day
    for i in np.flatnonzero(day.pos == 1):
        if not day.can_enter(i) or not np.isfinite(gap[i]) or not np.isfinite(atr[i]):
            continue
        lo, hi = low[i - 1:i + 1].min(), high[i - 1:i + 1].max()
        if gap[i] >= gap_min and close[i] > opened[i] and _sane(atr[i], close[i] - lo):
            plan.signals[i] = _signal(1, lo, target=close[i] + rr * (close[i] - lo),
                                      hold=day.hold[i], tag="gap_trend")
        elif gap[i] <= -gap_min and close[i] < opened[i] and _sane(atr[i], hi - close[i]):
            plan.signals[i] = _signal(-1, hi, target=close[i] - rr * (hi - close[i]),
                                      hold=day.hold[i], tag="gap_trend")
    return plan


GAP_TREND = Strategy(
    name="gap_trend15",
    hypothesis="A gap of at least `gap` that the first half hour confirms — price still "
               "on the gap's side of the open — keeps going.",
    works_when="Gaps on news that the market accepts.",
    fails_when="Gaps that are filled in the first hour.",
    falsified_by="Net expectancy not above zero after costs, or no better than random.",
    entry_rule="Today's open at least `gap` away from yesterday's close, and the close of "
               "the second bar (09:30) on the gap's side of the open: enter at the next open.",
    exit_rule="Stop beyond the extreme of the first two bars; target `rr` times that risk; "
              "out at 15:15. One trade a day.",
    grid=_grid(gap=(0.003, 0.007), rr=(1.5, 2.5)),
    build=_gap_trend,
    warmup=lambda p: 400,
)


def _gap_reversal(df: pd.DataFrame, params: Params) -> Plan:
    gap_min, decide = float(params["gap"]), int(params["decide"])
    _, high, low, close = _bars(df)
    atr, day, plan = _atr(df), Day(df), Plan()
    gap = _gap_state(df, day)
    opened = day.open_of_day
    for i in np.flatnonzero(day.pos == decide):
        if not day.can_enter(i) or not np.isfinite(gap[i]) or not np.isfinite(atr[i]):
            continue
        lo, hi = low[day.start[i]:i + 1].min(), high[day.start[i]:i + 1].max()
        fill = day.prev_close[i]
        if gap[i] >= gap_min and close[i] < opened[i] and _sane(atr[i], hi - close[i]) \
                and close[i] - fill > 0:
            plan.signals[i] = _signal(-1, hi, target=fill, hold=day.hold[i], tag="gap_reversal")
        elif gap[i] <= -gap_min and close[i] > opened[i] and _sane(atr[i], close[i] - lo) \
                and fill - close[i] > 0:
            plan.signals[i] = _signal(1, lo, target=fill, hold=day.hold[i], tag="gap_reversal")
    return plan


GAP_REVERSAL = Strategy(
    name="gap_reversal15",
    hypothesis="A gap of at least `gap` whose first bars close back toward yesterday's "
               "close is being faded, and fills.",
    works_when="Gaps on thin news; opening auction overreaction.",
    fails_when="Gaps that are the start of a trend day.",
    falsified_by="Net expectancy not above zero after costs, or no better than random; "
                 "and not different from `gap_trend15` in the opposite direction.",
    entry_rule="Gap at least `gap`, and at the close of bar `decide` (0 is 09:15, 1 is "
               "09:30) price is back on the prior-close side of the open: enter against "
               "the gap at the next open.",
    exit_rule="Target yesterday's close; stop beyond the day's extreme so far; out at 15:15.",
    grid=_grid(gap=(0.004, 0.008), decide=(0.0, 1.0)),
    build=_gap_reversal,
    warmup=lambda p: 400,
)


CANDIDATES: Tuple[Strategy, ...] = (
    ORB, TREND, TREND_VOL, SUPERTREND, VWAP, SQUEEZE, RSI_MOMENTUM, CHANNEL, GAP_TREND,
    GAP_REVERSAL)
BY_NAME: Dict[str, Strategy] = {s.name: s for s in CANDIDATES}

#: Hypotheses in the brief that this module does not implement.
NOT_BUILT: Mapping[str, str] = {
    "cross_sectional_momentum": "Ranks stocks against each other at a timestamp; the "
        "per-stock Strategy interface cannot express it, and it needs a point-in-time "
        "universe the data does not have (the universe is today's index members).",
    "daily_mean_reversion": "Daily bars on delivery; a separate study (delivery costs "
        "and a different holding period), not started.",
}


def trials() -> int:
    """Parameter combinations declared across every candidate."""
    return sum(len(s.grid) for s in CANDIDATES)


def declared() -> List[Dict[str, object]]:
    """The register of what was declared, for the protocol file."""
    return [{"name": s.name, "hypothesis": s.hypothesis, "entry_rule": s.entry_rule,
             "exit_rule": s.exit_rule, "falsified_by": s.falsified_by,
             "grid": [dict(g) for g in s.grid]} for s in CANDIDATES]
