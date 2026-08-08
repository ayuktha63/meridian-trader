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

COLUMNS = ("time", "open", "high", "low", "close", "volume")


def bar_open_time(ts: float, tf_seconds: int) -> int:
    """Floor a timestamp to its bar-open boundary.

    Weekly bars are anchored to Monday 00:00 UTC (epoch was a Thursday, so
    the 3-day offset matters).
    """
    ts = int(ts)
    if tf_seconds == 604800:
        return ((ts + 345_600) // 604_800) * 604_800 - 345_600
    return (ts // tf_seconds) * tf_seconds


class CandleSeries:
    __slots__ = ("symbol", "timeframe", "tf_seconds", "maxlen", "_rows",
                 "_version", "_frame", "_frame_version", "_lock")

    def __init__(self, symbol: str, timeframe: str, maxlen: int = 6000) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.tf_seconds = get_timeframe(timeframe).seconds
        self.maxlen = maxlen
        self._rows: List[List[float]] = []
        self._version = 0
        self._frame: Optional[pd.DataFrame] = None
        self._frame_version = -1
        self._lock = threading.RLock()

    # ── mutation ──────────────────────────────────────────────

    def replace(self, rows: List[List[float]]) -> None:
        with self._lock:
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
        """Fold a tick into the series. Returns (new_bar_opened, bar)."""
        with self._lock:
            open_time = bar_open_time(ts, self.tf_seconds)
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

            prev_close = self._rows[-1][4] if self._rows else price
            bar = [float(open_time), prev_close, max(prev_close, price),
                   min(prev_close, price), price, volume]
            self._rows.append(bar)
            if len(self._rows) > self.maxlen:
                self._rows = self._rows[-self.maxlen:]
            self._version += 1
            return True, list(bar)

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
