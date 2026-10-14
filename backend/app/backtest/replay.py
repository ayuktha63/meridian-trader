"""Replay the live engine over history, one bar at a time.

The backtest used to score bars with its own vectorised imitation of the
engine — faster, and not the code that trades. This module calls the real
thing instead: for each historical bar it builds the frames the engine would
have had at that bar's close and runs `analysis.engine.evaluate` and
`conclude` on them, exactly as `engine.run` does live.

What the replay cannot reproduce, stated so a result is not read as more
than it is:

* **Claude.** The deterministic analyst always supplies the verdict, as it
  does live whenever Claude is off or unavailable.
* **Drawings.** The trader's own levels are not part of history.
* **Timeframes below the chart's.** Live fuses 1m/5m/15m into a 1h read;
  minute history that deep does not exist, so only the chart timeframe and
  those above it are scored. They carry about a fifth of the trend
  component's weight.
* **The tick.** Live prices a signal off the latest quote inside a forming
  bar; replay sees each bar once, at its close.

Higher timeframes are rebuilt causally: completed bars come from stored
history, and the bar still forming at the decision time is aggregated from
the chart's own bars up to that moment — never read from a stored bar that
already knows how its period ended.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..analysis import engine
from ..data.sessions import BarGrid
from ..data.store import COLUMNS, bar_open_time
from ..instruments import MTF_LADDER, Instrument, get_timeframe
from ..schemas.analysis import Decision

log = logging.getLogger("meridian.backtest.replay")

#: What the live engine analyses and what it hands the multi-timeframe fusion.
ANALYSIS_BARS = engine.ANALYSIS_BARS
MTF_BARS = 400
MIN_BARS = engine.MIN_BARS

#: Evaluations kept in memory. A pass over a few thousand bars takes on the
#: order of a minute; a second strategy or a walk-forward on the same market
#: should not pay for it again.
CACHE_LIMIT = 40_000


@dataclass(frozen=True)
class Setup:
    """A trade the engine or a named strategy would take at a bar's close."""
    direction: str          # BUY | SELL
    entry: float
    stop: float
    target: float
    confidence: float
    reason: str

    @property
    def risk_reward(self) -> float:
        risk = abs(self.entry - self.stop)
        return abs(self.target - self.entry) / risk if risk > 0 else 0.0


@dataclass
class BarSignal:
    #: The fused verdict — what the trade panel shows and Execute Live sends.
    engine: Optional[Setup] = None
    #: Each named strategy from the Plays tab that took a side, by key.
    plays: Dict[str, Setup] = field(default_factory=dict)


def _valid(direction: str, entry, stop, target) -> bool:
    if not entry or not stop or not target:
        return False
    return (stop < entry < target) if direction == "BUY" else (target < entry < stop)


def bar_signal(window: pd.DataFrame, frames: Dict[str, pd.DataFrame],
               instrument: Instrument, timeframe: str, *,
               daily: Optional[pd.DataFrame] = None,
               balance: float, risk_pct: float) -> BarSignal:
    """What the live engine concludes from these frames."""
    price = float(window["close"].iloc[-1])
    evaluation = engine.evaluate(
        window, frames, instrument, timeframe, price, daily=daily,
        balance=balance, risk_pct=risk_pct)
    outcome = engine.conclude(evaluation, instrument, price, fallback_reason="replay")

    signal = BarSignal()
    verdict, plan = outcome.verdict, outcome.plan
    if verdict.decision != Decision.WAIT and _valid(
            verdict.decision.value, plan.entry, plan.stop_loss, plan.take_profit):
        signal.engine = Setup(
            direction=verdict.decision.value, entry=plan.entry, stop=plan.stop_loss,
            target=plan.take_profit, confidence=outcome.confidence.final_confidence,
            reason="{} at {:.0f}% — {}".format(
                verdict.decision.value, outcome.confidence.final_confidence,
                outcome.confidence.reason))
    for play in (evaluation.strategies.results if evaluation.strategies else []):
        direction = {"long": "BUY", "short": "SELL"}.get(play.stance)
        if direction and _valid(direction, play.entry, play.stop_loss, play.take_profit):
            signal.plays[play.key] = Setup(
                direction=direction, entry=play.entry, stop=play.stop_loss,
                target=play.take_profit, confidence=play.conviction,
                reason="{} — {} of {} conditions met".format(
                    play.name, play.conditions_met, play.conditions_total))
    return signal


class FrameBuilder:
    """The frames the engine would have had at the close of any bar."""

    def __init__(self, base: pd.DataFrame, timeframe: str,
                 higher: Optional[Dict[str, pd.DataFrame]] = None,
                 grid: Optional[BarGrid] = None) -> None:
        """`grid` is the session the venue stamps its bars on, where it has
        one: an hour is then the venue's hour (09:15 to 10:15 on the NSE),
        and the bar "still forming" at a close is cut where the stored
        history of that timeframe cuts it."""
        self.timeframe = timeframe
        self.grid = grid
        self.seconds = get_timeframe(timeframe).seconds
        self.base = base.reset_index(drop=True)
        self.values = self.base[list(COLUMNS)].to_numpy(dtype=float)
        self.times = self.values[:, 0]
        self.higher: Dict[str, Tuple[int, np.ndarray]] = {}
        for tf in MTF_LADDER:
            seconds = get_timeframe(tf).seconds
            if seconds <= self.seconds:
                continue
            stored = (higher or {}).get(tf)
            if stored is not None and len(stored) >= MIN_BARS:
                rows = stored[list(COLUMNS)].to_numpy(dtype=float)
            else:
                rows = self._resample(seconds)
            self.higher[tf] = (seconds, rows[np.argsort(rows[:, 0], kind="stable")])

    def _resample(self, seconds: int) -> np.ndarray:
        """Higher-timeframe bars built from the chart's own bars — used when
        no stored history exists for that timeframe."""
        if len(self.times) == 0:
            # Nothing to resample from. A symbol/timeframe with no bars at
            # all is "no higher-timeframe data", not a crash — the same rule
            # every other empty-history path in this app follows.
            return np.empty((0, len(COLUMNS)), dtype=float)
        opens = np.array([bar_open_time(t, seconds, self.grid) for t in self.times],
                         dtype=float)
        rows: List[List[float]] = []
        for start in np.flatnonzero(np.r_[True, opens[1:] != opens[:-1]]):
            end = start
            while end + 1 < len(opens) and opens[end + 1] == opens[start]:
                end += 1
            chunk = self.values[start:end + 1]
            rows.append([opens[start], chunk[0, 1], chunk[:, 2].max(), chunk[:, 3].min(),
                         chunk[-1, 4], chunk[:, 5].sum()])
        return np.array(rows, dtype=float).reshape(-1, len(COLUMNS))

    def window(self, i: int) -> pd.DataFrame:
        return self.base.iloc[max(0, i + 1 - ANALYSIS_BARS): i + 1]

    def _as_of(self, seconds: int, rows: np.ndarray, i: int) -> np.ndarray:
        """A higher timeframe as it stood at the close of bar `i`: the bars
        that had finished, then the one still forming, built from the chart's
        own bars up to that moment."""
        decided_at = self.times[i] + self.seconds
        forming_open = float(bar_open_time(self.times[i], seconds, self.grid))
        # Completed means its period had ended by the decision time *and* it
        # is not the bar being formed. The first test alone is not enough for
        # daily bars stamped at local midnight; the second alone would trust
        # a stored bar that already knows its close.
        done = rows[(rows[:, 0] + seconds <= decided_at) & (rows[:, 0] < forming_open)]
        first = int(np.searchsorted(self.times, forming_open, side="left"))
        chunk = self.values[first: i + 1]
        forming = np.array([[forming_open, chunk[0, 1], chunk[:, 2].max(),
                             chunk[:, 3].min(), chunk[-1, 4], chunk[:, 5].sum()]])
        return np.vstack([done[-(MTF_BARS - 1):], forming])

    def frames(self, i: int) -> Dict[str, pd.DataFrame]:
        """What `engine._frames_for_mtf` would have returned at bar `i`."""
        frames: Dict[str, pd.DataFrame] = {}
        if self.timeframe in MTF_LADDER:
            frames[self.timeframe] = self.base.iloc[max(0, i + 1 - MTF_BARS): i + 1]
        for tf, (seconds, rows) in self.higher.items():
            frame = self._as_of(seconds, rows, i)
            if len(frame) >= MIN_BARS:
                frames[tf] = pd.DataFrame(frame, columns=list(COLUMNS))
        return frames

    def daily(self, i: int) -> Optional[pd.DataFrame]:
        """The daily bars the live engine reads pivots from — the last 60,
        today's still forming — or None, exactly when live would have none."""
        if self.timeframe == "1d":
            frame = self.base.iloc[max(0, i + 1 - 60): i + 1]
        elif "1d" in self.higher:
            seconds, rows = self.higher["1d"]
            frame = pd.DataFrame(self._as_of(seconds, rows, i)[-60:], columns=list(COLUMNS))
        else:
            return None                    # a weekly chart: no daily history below it
        return frame if len(frame) >= 2 else None


_cache: Dict[Tuple[str, str], Dict[int, Tuple[tuple, BarSignal]]] = {}


def clear_cache() -> None:
    _cache.clear()


def _trim_cache() -> None:
    while sum(len(v) for v in _cache.values()) > CACHE_LIMIT and len(_cache) > 1:
        _cache.pop(next(iter(_cache)))


def signals(symbol: str, timeframe: str, base: pd.DataFrame,
            higher: Optional[Dict[str, pd.DataFrame]], instrument: Instrument,
            indices: Iterable[int], *, balance: float = 10_000.0,
            risk_pct: float = 1.0, grid: Optional[BarGrid] = None) -> Dict[int, BarSignal]:
    """The engine's conclusion at the close of each bar in `indices`.

    Results are remembered per (symbol, timeframe, bar): a bar that has
    closed gives the same answer every time, as long as the price history
    behind it is the same — which the fingerprint checks.
    """
    builder = FrameBuilder(base, timeframe, higher, grid)
    store = _cache.setdefault((symbol, timeframe), {})
    last = len(builder.times) - 1
    out: Dict[int, BarSignal] = {}
    for i in indices:
        if i < MIN_BARS - 1 or i > last:
            continue
        frames = builder.frames(i)
        daily = builder.daily(i)
        key = int(builder.times[i])
        window = builder.window(i)
        fingerprint = (
            round(float(builder.values[i, 4]), 10), len(window),
            round(float(window["close"].iloc[0]), 10),
            tuple(sorted((tf, len(frame), round(float(frame["close"].iloc[0]), 10))
                         for tf, frame in frames.items())),
            0 if daily is None else len(daily))
        cached = store.get(key)
        if cached is not None and cached[0] == fingerprint:
            out[i] = cached[1]
            continue
        try:
            signal = bar_signal(window, frames, instrument, timeframe, daily=daily,
                                balance=balance, risk_pct=risk_pct)
        except Exception:  # noqa: BLE001 - one unreadable bar is a WAIT, not a failed run
            log.exception("replay failed at %s %s bar %s", symbol, timeframe, key)
            signal = BarSignal()
        out[i] = signal
        if i < last:            # the newest bar may still be forming
            store[key] = (fingerprint, signal)
    _trim_cache()
    return out
