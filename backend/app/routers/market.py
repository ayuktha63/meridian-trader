from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from ..data.feed import feed
from ..data.sessions import market_clock
from ..data.store import store
from ..instruments import (
    DEFAULT_STREAM_SYMBOLS, INSTRUMENTS, SYMBOLS, TIMEFRAMES,
    get_instrument, get_timeframe,
)
from ..schemas.market import (
    Candle, CandleBatch, MarketClock, QuoteSnapshot, SymbolInfo, TimeframeInfo,
)

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/symbols", response_model=List[SymbolInfo])
async def list_symbols() -> List[SymbolInfo]:
    out: List[SymbolInfo] = []
    for symbol in SYMBOLS:
        inst = INSTRUMENTS[symbol]
        out.append(SymbolInfo(
            symbol=inst.symbol, display=inst.display, asset_class=inst.asset_class,
            digits=inst.digits, pip_size=inst.pip_size,
            contract_size=inst.contract_size,
            typical_spread_pips=inst.typical_spread_pips,
            quote_currency=inst.quote_currency,
            live=feed.provider_for(symbol) != "synthetic",
            venue=inst.venue,
            exchange=inst.exchange,
            broker_symbol=inst.broker_symbol,
            tradable_in_india=inst.tradable_in_india,
            note=inst.note,
            provider=feed.provider_for(symbol),
        ))
    return out


@router.get("/timeframes", response_model=List[TimeframeInfo])
async def list_timeframes() -> List[TimeframeInfo]:
    return [TimeframeInfo(code=t.code, label=t.label, seconds=t.seconds)
            for t in TIMEFRAMES.values()]


@router.get("/candles", response_model=CandleBatch)
async def get_candles(
    symbol: str = Query(..., description="e.g. EURUSD"),
    timeframe: str = Query("15m"),
    limit: int = Query(600, ge=50, le=5000),
) -> CandleBatch:
    try:
        get_instrument(symbol)
        get_timeframe(timeframe)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    await feed.ensure_history(symbol, timeframe)
    rows = store.series(symbol, timeframe).dicts(limit)
    if not rows:
        raise HTTPException(
            status_code=503,
            detail="No data available yet for {} {}".format(symbol, timeframe))

    return CandleBatch(
        symbol=symbol.upper(),
        timeframe=timeframe.lower(),
        candles=[Candle(**row) for row in rows],
        provider=feed.provider_for(symbol),
        partial_bar=Candle(**rows[-1]),
    )


@router.get("/quote/{symbol}", response_model=QuoteSnapshot)
async def get_quote(symbol: str) -> QuoteSnapshot:
    quote = feed.quote(symbol)
    if quote is None:
        await feed.ensure_history(symbol, "1m")
        quote = feed.quote(symbol)
    if quote is None:
        raise HTTPException(status_code=503, detail="No quote available for " + symbol)
    return quote


@router.get("/quotes", response_model=Dict[str, QuoteSnapshot])
async def get_quotes(symbols: Optional[str] = None) -> Dict[str, QuoteSnapshot]:
    wanted = ([s.strip().upper() for s in symbols.split(",")]
          if symbols else DEFAULT_STREAM_SYMBOLS)
    out: Dict[str, QuoteSnapshot] = {}
    for symbol in wanted:
        quote = feed.quote(symbol)
        if quote is not None:
            out[symbol] = quote
    return out


@router.get("/clock", response_model=MarketClock)
async def get_clock() -> MarketClock:
    return market_clock()


@router.get("/status")
async def get_status() -> Dict[str, object]:
    return feed.status()
