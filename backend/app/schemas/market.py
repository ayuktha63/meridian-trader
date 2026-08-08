from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class Candle(BaseModel):
    """A single OHLCV bar. `time` is the bar-open unix timestamp in seconds."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def bullish(self) -> bool:
        return self.close >= self.open


class Tick(BaseModel):
    symbol: str
    time: float
    bid: float
    ask: float
    last: float
    volume: float = 0.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


class CandleBatch(BaseModel):
    symbol: str
    timeframe: str
    candles: List[Candle]
    provider: str = "synthetic"
    partial_bar: Optional[Candle] = None


class SymbolInfo(BaseModel):
    symbol: str
    display: str
    asset_class: str
    digits: int
    pip_size: float
    contract_size: float
    typical_spread_pips: float
    quote_currency: str
    live: bool = False
    #: Where the instrument is actually traded, and under what name.
    venue: str = "otc"
    exchange: Optional[str] = None
    broker_symbol: Optional[str] = None
    tradable_in_india: bool = False
    note: str = ""
    provider: str = "synthetic"


class TimeframeInfo(BaseModel):
    code: str
    label: str
    seconds: int


class QuoteSnapshot(BaseModel):
    symbol: str
    bid: float
    ask: float
    last: float
    spread_pips: float
    change_pct: float = 0.0
    change_abs: float = 0.0
    day_high: float = 0.0
    day_low: float = 0.0
    updated_at: float = 0.0
    live: bool = False


class MarketSession(BaseModel):
    name: str
    open: bool
    opens_in_minutes: Optional[int] = None
    closes_in_minutes: Optional[int] = None


class MarketClock(BaseModel):
    utc_time: str
    sessions: List[MarketSession] = Field(default_factory=list)
    active_session: str = "closed"
    liquidity_regime: str = "normal"   # thin | normal | high | overlap
