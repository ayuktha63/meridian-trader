from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Sequence

from ..sessions import BarGrid


@dataclass
class ProviderTick:
    symbol: str
    time: float
    price: float
    bid: float
    ask: float
    volume: float = 0.0


TickHandler = Callable[[ProviderTick], Awaitable[None]]


def resample_rows(rows: List[List[float]], tf_seconds: int,
                  grid: Optional[BarGrid] = None) -> List[List[float]]:
    """Aggregate finer candles into coarser ones.

    Rows are [time, open, high, low, close, volume] ascending. Buckets are
    aligned the way the candle store aligns live bars (`bar_open_time`), so a
    resampled history and the bars built from ticks afterwards line up —
    weekly bars on Monday included. `grid` is the venue's session, where it
    has one: a four-hour bar is then counted from the session open, and a
    week from local Monday, not from UTC's.
    """
    from ..store import bar_open_time

    if not rows or tf_seconds <= 0:
        return rows
    out: List[List[float]] = []
    for time_s, open_p, high, low, close, volume in rows:
        start = float(bar_open_time(time_s, tf_seconds, grid))
        if out and out[-1][0] == start:
            bar = out[-1]
            bar[2], bar[3], bar[4] = max(bar[2], high), min(bar[3], low), close
            bar[5] += volume
        else:
            out.append([start, open_p, high, low, close, volume])
    return out


class MarketDataProvider(abc.ABC):
    """Contract every data source implements.

    `history` returns rows of [time, open, high, low, close, volume] sorted
    ascending with the most recent (possibly forming) bar last.
    `stream` is a long-running task that pushes ticks to `handler` until
    cancelled.
    """

    name: str = "base"
    live: bool = False

    @abc.abstractmethod
    def supports(self, symbol: str) -> bool:
        ...

    @abc.abstractmethod
    async def history(self, symbol: str, timeframe: str, bars: int) -> List[List[float]]:
        ...

    @abc.abstractmethod
    async def stream(self, symbols: Sequence[str], handler: TickHandler) -> None:
        ...

    async def prepare(self) -> None:
        """Called once before the feed asks which symbols this source serves:
        load whatever reference data that answer depends on."""
        return None

    def bar_grid(self, symbol: str) -> Optional[BarGrid]:
        """The session grid this source's bars for `symbol` are stamped on,
        or None if they sit on the plain clock grid."""
        return None

    async def deep_history(self, symbol: str, timeframe: str, bars: int) -> List[List[float]]:
        """History for a research snapshot: as far back as the venue goes, up
        to `bars`. `history` is capped at what a chart or a quick backtest
        needs; a provider that can page further overrides this."""
        return await self.history(symbol, timeframe, bars)

    async def close(self) -> None:
        return None
