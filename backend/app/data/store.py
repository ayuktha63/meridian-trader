"""In-memory OHLCV store.

One `CandleSeries` per (symbol, timeframe). Series are append-mostly: the
feed mutates the forming bar in place and appends when a bar boundary is
crossed. A version counter lets the DataFrame view be cached so the
analysis engine never rebuilds a frame that has not changed.
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..instruments import get_timeframe
from .sessions import BarGrid

COLUMNS = ("time", "open", "high", "low", "close", "volume")


def bar_open_time(ts: float, tf_seconds: int, grid: Optional[BarGrid] = None) -> int:
    """Floor a timestamp to its bar-open boundary.

    With a `grid` — an exchange's session — the boundary is the venue's own:
    counted from the session open, daily bars at local midnight. Without
    one, the clock's: weekly bars anchored to Monday 00:00 UTC. The epoch
    was a Thursday, so the Monday before it is three days back — the offset
    was four, which quietly started every week on a Sunday.
    """
    if grid is not None:
        return grid.bucket(ts, tf_seconds)
    ts = int(ts)
    if tf_seconds == 604800:
        return ((ts + 259_200) // 604_800) * 604_800 - 259_200
    return (ts // tf_seconds) * tf_seconds


class CandleSeries:
    __slots__ = ("symbol", "timeframe", "tf_seconds", "maxlen", "source", "grid", "_rows",
                 "_version", "_frame", "_frame_version", "_lock")

    def __init__(self, symbol: str, timeframe: str, maxlen: int = 6000) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.tf_seconds = get_timeframe(timeframe).seconds
        self.maxlen = maxlen
        #: The provider these bars came from. None until something has
        #: loaded the series.
        self.source: Optional[str] = None
        #: The session grid of the venue these bars came from, or None for
        #: the plain clock grid. Set with the bars, by whoever loads them.
        self.grid: Optional[BarGrid] = None
        self._rows: List[List[float]] = []
        self._version = 0
        self._frame: Optional[pd.DataFrame] = None
        self._frame_version = -1
        self._lock = threading.RLock()

    # ── mutation ──────────────────────────────────────────────

    def replace(self, rows: List[List[float]], source: Optional[str] = None,
                grid: Optional[BarGrid] = None) -> None:
        with self._lock:
            self.source = source if rows else None
            self.grid = grid if rows else None
            rows = sorted(rows, key=lambda r: r[0])
            deduped: List[List[float]] = []
            for row in rows:
                if deduped and deduped[-1][0] == row[0]:
                    deduped[-1] = row
                else:
                    deduped.append(list(row))
            self._rows = deduped[-self.maxlen:]
            self._version += 1

    def apply_price(self, ts: float, price: float, volume: float = 0.0) -> Tuple[bool, List[float]]:
        """Fold a tick into the series. Returns (new_bar_opened, bar).

        On an exchange's grid three things differ from a market that never
        shuts, and each was a bar that did not exist:

        * A tick outside the session builds nothing. The quote poll runs all
          night, and every fifteen minutes it opened a flat bar at the last
          price — seventy a night, all of them read by the indicators.
        * Inside the session a new bar is opened only by a tick that shows
          trading: a traded volume, or a price that moved. A holiday looks,
          to the clock, like any weekday.
        * A new bar opens at the first price seen in it. Opening it at the
          previous close is a convention for a continuous market; across a
          night it draws the gap into the first bar's range.
        """
        with self._lock:
            grid = self.grid
            if grid is not None and not grid.in_session(ts):
                return False, self._last_or(ts, price)
            open_time = bar_open_time(ts, self.tf_seconds, grid)
            if self._rows and self._rows[-1][0] == open_time:
                bar = self._rows[-1]
                bar[2] = max(bar[2], price)
                bar[3] = min(bar[3], price)
                bar[4] = price
                bar[5] += volume
                self._version += 1
                return False, list(bar)

            if self._rows and open_time < self._rows[-1][0]:
                return False, list(self._rows[-1])

            if grid is not None:
                if self._rows and volume <= 0 and price == self._rows[-1][4]:
                    return False, list(self._rows[-1])      # nothing has traded
                prev_close = price
            else:
                prev_close = self._rows[-1][4] if self._rows else price
            bar = [float(open_time), prev_close, max(prev_close, price),
                   min(prev_close, price), price, volume]
            self._rows.append(bar)
            if len(self._rows) > self.maxlen:
                self._rows = self._rows[-self.maxlen:]
            self._version += 1
            return True, list(bar)

    def _last_or(self, ts: float, price: float) -> List[float]:
        """The last bar, or — for a series with none — a bar that is not
        stored, so that a caller always has one to read."""
        if self._rows:
            return list(self._rows[-1])
        return [float(bar_open_time(ts, self.tf_seconds, self.grid)), price, price, price,
                price, 0.0]

    # ── access ────────────────────────────────────────────────

    @property
    def version(self) -> int:
        return self._version

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def last_close(self) -> Optional[float]:
        return self._rows[-1][4] if self._rows else None

    @property
    def last_time(self) -> Optional[int]:
        return int(self._rows[-1][0]) if self._rows else None

    def rows(self, limit: Optional[int] = None) -> List[List[float]]:
        with self._lock:
            data = self._rows[-limit:] if limit else self._rows
            return [list(r) for r in data]

    def dicts(self, limit: Optional[int] = None) -> List[Dict[str, float]]:
        return [
            {
                "time": int(r[0]), "open": r[1], "high": r[2],
                "low": r[3], "close": r[4], "volume": r[5],
            }
            for r in self.rows(limit)
        ]

    def bar_end(self, open_time: float) -> float:
        """When the bar that opened at `open_time` is complete."""
        if self.grid is not None:
            return float(self.grid.bar_end(open_time, self.tf_seconds))
        return float(open_time) + self.tf_seconds

    def forming(self, now: float) -> bool:
        """Whether the last bar is still being built at `now`."""
        with self._lock:
            return bool(self._rows) and now < self.bar_end(self._rows[-1][0])

    def completed_frame(self, now: float, limit: Optional[int] = None) -> pd.DataFrame:
        """The bars that had finished by `now` — what a decision may be made
        on. The bar still forming is a guess at its own close: a breakout
        that is there at twenty past and gone by the half hour was never one,
        and no backtest ever saw it."""
        with self._lock:
            frame = self.frame()
            if self.forming(now):
                frame = frame.iloc[:-1]
        frame = frame.tail(limit) if limit else frame
        return frame.reset_index(drop=True)

    def frame(self, limit: Optional[int] = None) -> pd.DataFrame:
        """Cached DataFrame view; rebuilt only when the series changed."""
        with self._lock:
            if self._frame is None or self._frame_version != self._version:
                if not self._rows:
                    self._frame = pd.DataFrame(columns=list(COLUMNS), dtype=float)
                else:
                    arr = np.asarray(self._rows, dtype=float)
                    self._frame = pd.DataFrame(arr, columns=list(COLUMNS))
                self._frame_version = self._version
            frame = self._frame
        return frame.tail(limit).reset_index(drop=True) if limit else frame


class CandleStore:
    def __init__(self) -> None:
        self._series: Dict[Tuple[str, str], CandleSeries] = {}
        self._lock = threading.RLock()

    def series(self, symbol: str, timeframe: str) -> CandleSeries:
        key = (symbol.upper(), timeframe.lower())
        with self._lock:
            if key not in self._series:
                self._series[key] = CandleSeries(key[0], key[1])
            return self._series[key]

    def has(self, symbol: str, timeframe: str) -> bool:
        return (symbol.upper(), timeframe.lower()) in self._series

    def frame(self, symbol: str, timeframe: str, limit: Optional[int] = None) -> pd.DataFrame:
        return self.series(symbol, timeframe).frame(limit)

    def keys(self) -> List[Tuple[str, str]]:
        with self._lock:
            return list(self._series.keys())

    def clear(self) -> None:
        with self._lock:
            self._series.clear()


store = CandleStore()
