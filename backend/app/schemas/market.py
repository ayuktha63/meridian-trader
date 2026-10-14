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
    provider: str = ""
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
    provider: str = ""


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
    #: How old the quote was when it was read, and whether that is too old
    #: to act on. A quote does not expire by itself: when the feed stops,
    #: the last one sits there looking like a price.
    age_seconds: float = 0.0
    stale: bool = False


class MarketSession(BaseModel):
    name: str
    open: bool
    opens_in_minutes: Optional[int] = None
    closes_in_minutes: Optional[int] = None


class MarketClock(BaseModel):
    utc_time: str
    #: Exchange time. Every market here is an Indian one.
    ist_time: str = ""
    sessions: List[MarketSession] = Field(default_factory=list)
    active_session: str = "closed"
    liquidity_regime: str = "normal"   # closed | normal | high
    #: What "open" rests on — the clock, which does not know a holiday.
    basis: str = ""
