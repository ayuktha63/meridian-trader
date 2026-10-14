"""NSE-shaped history for the tests: sessions, holidays and a broker that
answers from memory. Nothing here exists outside the test suite."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from app.data.angel_history import INTERVALS, OK, HistoryReport, Scrip, Window

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_MINUTES = 375


def ist(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=IST).timestamp()


def weekdays(start: date, count: int, holidays: Iterable[date] = ()) -> List[date]:
    """The next `count` trading days from `start`: Monday to Friday, less holidays."""
    closed, out, day = set(holidays), [], start
    while len(out) < count:
        if day.weekday() < 5 and day not in closed:
            out.append(day)
        day += timedelta(days=1)
    return out


def _path(count: int, seed: int, price: float) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return price * np.exp(np.cumsum(rng.normal(0.0002, 0.004, count)))


def daily_rows(days: Sequence[date], seed: int = 1, price: float = 1000.0,
               volume: float = 50_000.0) -> List[List[float]]:
    """One bar a day, stamped the way the venue stamps them: local midnight."""
    closes = _path(len(days), seed, price)
    rows = []
    for i, (day, close) in enumerate(zip(days, closes)):
        open_ = closes[i - 1] if i else close
        rows.append([ist(day.year, day.month, day.day), open_, max(open_, close) * 1.002,
                     min(open_, close) * 0.998, close, volume + i % 11])
    return rows


def intraday_rows(days: Sequence[date], minutes: int, seed: int = 1, price: float = 1000.0,
                  volume: float = 900.0) -> List[List[float]]:
    """Full sessions of `minutes`-long bars from 09:15, the last one short."""
    per_day = -(-SESSION_MINUTES // minutes)
    closes = _path(len(days) * per_day, seed, price)
    rows = []
    for d, day in enumerate(days):
        opened = ist(day.year, day.month, day.day, 9, 15)
        for b in range(per_day):
            i = d * per_day + b
            open_ = closes[i - 1] if i else closes[i]
            rows.append([opened + b * minutes * 60, open_, max(open_, closes[i]) * 1.0005,
                         min(open_, closes[i]) * 0.9995, closes[i], volume + i % 13])
    return rows


def on(rows: Sequence[Sequence[float]], day: date) -> List[int]:
    """Indices of the rows that fall on this local day."""
    lo = ist(day.year, day.month, day.day)
    return [i for i, row in enumerate(rows) if lo <= row[0] < lo + 86_400]


def without(rows: Sequence[Sequence[float]], drop: Iterable[int]) -> List[List[float]]:
    gone = set(drop)
    return [list(row) for i, row in enumerate(rows) if i not in gone]


class FakeBroker:
    """A history source that answers from what it was given.

    `series` maps a trading symbol ("RELIANCE-EQ", "Nifty 50") to its rows.
    A symbol in `incomplete` comes back with a report that says a request
    failed; one that is not in `series` is not in the scrip master at all.
    """

    name = "angelone"

    def __init__(self, series: Dict[str, List[List[float]]],
                 incomplete: Iterable[str] = (), tokens: Optional[Dict[str, str]] = None):
        self.series = series
        self.incomplete = set(incomplete)
        self.tokens = tokens or {}
        self.asked: List[Tuple[str, str]] = []

    def token(self, trading_symbol: str) -> str:
        return self.tokens.get(trading_symbol, str(1000 + sorted(self.series).index(trading_symbol)))

    async def scrip(self, exchange: str, trading_symbol: str) -> Optional[Scrip]:
        if trading_symbol not in self.series:
            return None
        return Scrip(exchange=exchange, trading_symbol=trading_symbol,
                     token=self.token(trading_symbol))

    async def candles(self, scrip: Scrip, timeframe: str, start: datetime,
                      end: datetime) -> Tuple[List[List[float]], HistoryReport]:
        self.asked.append((scrip.trading_symbol, timeframe))
        rows = [list(r) for r in self.series[scrip.trading_symbol]
                if start.timestamp() <= r[0] <= end.timestamp()]
        broken = scrip.trading_symbol in self.incomplete
        report = HistoryReport(
            exchange=scrip.exchange, trading_symbol=scrip.trading_symbol, token=scrip.token,
            timeframe=timeframe, interval=INTERVALS[timeframe],
            requested_from=start.isoformat(timespec="minutes"),
            requested_to=end.isoformat(timespec="minutes"), rows=len(rows),
            received_from=rows[0][0] if rows else None,
            received_to=rows[-1][0] if rows else None, complete=not broken,
            incomplete_reason="1 of 3 requests failed (AB1004 try later)." if broken else None,
            retrieved_at=end.timestamp(),
            windows=[Window("2024-01-01 00:00", "2025-12-31 00:00", OK, len(rows), 1)])
        return rows, report
