"""Angel One SmartAPI market data — real NSE/BSE/NFO/CDS/MCX prices from the
same broker connection already used for live execution.

Two things distinguish this from Zerodha's provider:

* **No separate subscription.** This reuses the Angel One session set up in
  Settings → Broker (see TRADING.md §2a) — no extra signup, no paid plan.
  It only ever activates once that session is connected.
* **Polling, not a websocket.** Angel One's SmartWebSocketV2 would give
  tighter latency, but it needs its own reconnect/threading machinery on top
  of what §2a already built. Polling `getMarketData` (a single batched
  request for every symbol at once) every few seconds is simpler, well
  within Angel One's REST rate limits for a normal watchlist, and gives the
  same "live, not simulated" guarantee this platform cares about — the
  number on screen is Angel One's actual last-traded price, just refreshed
  every few seconds instead of every tick.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from ...broker.angelone.auth import angel_auth
from ...broker.angelone.instruments import InstrumentUnsupported, angel_instruments
from ...instruments import get_instrument
from .base import MarketDataProvider, ProviderTick, TickHandler

log = logging.getLogger("meridian.angelone.marketdata")

#: Meridian timeframe -> Angel One's candle interval constant.
INTERVALS: Dict[str, str] = {
    "1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE", "1h": "ONE_HOUR", "4h": "ONE_HOUR",
    "1d": "ONE_DAY", "1w": "ONE_DAY",
}

#: How far back a single history request reaches, per interval. Angel One
#: caps this server-side; a fixed generous window is simpler than chunking
#: and covers every bar count this platform actually asks for.
LOOKBACK_DAYS: Dict[str, int] = {
    "ONE_MINUTE": 5, "FIVE_MINUTE": 30, "FIFTEEN_MINUTE": 60,
    "THIRTY_MINUTE": 90, "ONE_HOUR": 180, "ONE_DAY": 720,
}

POLL_SECONDS = 3.0


class AngelOneMarketDataProvider(MarketDataProvider):
    name = "angelone"
    live = True

    def __init__(self) -> None:
        self._last_error: Optional[str] = None

    def supports(self, symbol: str) -> bool:
        try:
            instrument = get_instrument(symbol)
        except KeyError:
            return False
        return (instrument.venue != "crypto" and instrument.tradable_in_india
                and angel_auth.connected)

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    # ── history ───────────────────────────────────────────────

    async def history(self, symbol: str, timeframe: str, bars: int) -> List[List[float]]:
        interval = INTERVALS.get(timeframe, "ONE_HOUR")
        try:
            match = await angel_instruments.resolve(symbol)
        except InstrumentUnsupported as exc:
            self._last_error = str(exc)
            return []

        to_date = datetime.now()
        from_date = to_date - timedelta(days=LOOKBACK_DAYS.get(interval, 90))
        params = {
            "exchange": match.exchange,
            "symboltoken": match.token,
            "interval": interval,
            "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
            "todate": to_date.strftime("%Y-%m-%d %H:%M"),
        }
        try:
            client = await asyncio.to_thread(angel_auth.ensure)
            response = await asyncio.to_thread(client.getCandleData, params)
        except Exception as exc:  # noqa: BLE001 - normalise every SDK/network failure
            self._last_error = "Angel One history request failed: {}".format(exc)
            log.warning(self._last_error)
            return []

        if not response or not response.get("status"):
            self._last_error = (response or {}).get("message", "no data returned")
            log.debug("Angel One history for %s: %s", symbol, self._last_error)
            return []

        rows: List[List[float]] = []
        for candle in response.get("data") or []:
            try:
                ts = datetime.fromisoformat(candle[0]).timestamp()
                rows.append([ts, float(candle[1]), float(candle[2]),
                           float(candle[3]), float(candle[4]), float(candle[5])])
            except (ValueError, IndexError, TypeError):
                continue  # one malformed row must not drop the whole series
        return rows[-bars:]

    # ── live ticks ────────────────────────────────────────────

    async def stream(self, symbols: Sequence[str], handler: TickHandler) -> None:
        while True:
            try:
                await self._poll_once(symbols, handler)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad poll must not kill the loop
                self._last_error = "Angel One quote poll failed: {}".format(exc)
                log.debug(self._last_error)
            await asyncio.sleep(POLL_SECONDS)

    async def _poll_once(self, symbols: Sequence[str], handler: TickHandler) -> None:
        by_exchange: Dict[str, List[str]] = {}
        token_to_symbol: Dict[str, str] = {}
        for symbol in symbols:
            try:
                match = await angel_instruments.resolve(symbol)
            except InstrumentUnsupported:
                continue
            by_exchange.setdefault(match.exchange, []).append(match.token)
            token_to_symbol[match.token] = symbol

        if not by_exchange:
            return

        client = await asyncio.to_thread(angel_auth.ensure)
        response = await asyncio.to_thread(client.getMarketData, "FULL", by_exchange)
        if not response or not response.get("status"):
            self._last_error = (response or {}).get("message", "no data returned")
            return
        self._last_error = None

        for row in (response.get("data") or {}).get("fetched") or []:
            token = row.get("symbolToken")
            symbol = token_to_symbol.get(token)
            if not symbol:
                continue
            try:
                ltp = float(row.get("ltp"))
            except (TypeError, ValueError):
                continue
            # This mode doesn't return a bid/ask — a tight synthetic spread
            # keeps the rest of the platform's cost model from seeing a
            # false zero-spread market, without inventing a real one.
            half_spread = ltp * 0.00025
            await handler(ProviderTick(
                symbol=symbol, time=time.time(), price=ltp,
                bid=ltp - half_spread, ask=ltp + half_spread,
                volume=float(row.get("tradeVolume") or 0.0),
            ))
