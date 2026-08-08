from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Sequence


@dataclass
class ProviderTick:
    symbol: str
    time: float
    price: float
    bid: float
    ask: float
    volume: float = 0.0


TickHandler = Callable[[ProviderTick], Awaitable[None]]


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

    async def close(self) -> None:
        return None
