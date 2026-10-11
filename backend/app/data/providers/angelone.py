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
from ...broker.angelone.orders import IST
from ...instruments import get_instrument, get_timeframe
from .base import MarketDataProvider, ProviderTick, TickHandler, resample_rows

log = logging.getLogger("meridian.angelone.marketdata")

#: Meridian timeframe -> Angel One's candle interval constant.
INTERVALS: Dict[str, str] = {
    "1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE", "1h": "ONE_HOUR", "1d": "ONE_DAY",
}

#: Timeframes with no native interval, built from a finer one. (These used
#: to be requested as ONE_HOUR and ONE_DAY and returned as they came — so a
#: "4h" series was hourly candles under another name, and the engine's 4H
#: read was scoring the wrong bars.)
DERIVED: Dict[str, str] = {"4h": "1h", "1w": "1d"}

#: How far back a single history request reaches, per interval. Angel One
#: caps this server-side, by interval; these sit inside the caps.
LOOKBACK_DAYS: Dict[str, int] = {
    "ONE_MINUTE": 5, "FIVE_MINUTE": 30, "FIFTEEN_MINUTE": 60,
    "THIRTY_MINUTE": 90, "ONE_HOUR": 180, "ONE_DAY": 720,
}

#: A walk-forward test needs more bars than one window holds, so a request
#: for them walks back window by window — this many at most, with a pause
#: that stays well inside the historical API's rate limit.
MAX_WINDOWS = 8
WINDOW_PAUSE_SECONDS = 0.4

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
        timeframe = timeframe.lower()
        if timeframe in DERIVED:
            source = DERIVED[timeframe]
            target = get_timeframe(timeframe).seconds
            factor = target // get_timeframe(source).seconds
            fine = await self.history(symbol, source, bars * factor)
            return resample_rows(fine, target)[-bars:]

        interval = INTERVALS.get(timeframe)
        if interval is None:
            return []
        try:
            match = await angel_instruments.resolve(symbol)
        except InstrumentUnsupported as exc:
            self._last_error = str(exc)
            return []

        window = timedelta(days=LOOKBACK_DAYS.get(interval, 90))
        end = datetime.now(IST)
        rows: Dict[float, List[float]] = {}
        empty = 0
        for attempt in range(MAX_WINDOWS):
            if attempt:
                await asyncio.sleep(WINDOW_PAUSE_SECONDS)
            chunk = await self._window(symbol, match, interval, end - window, end)
            if chunk is None:
                break                      # the request failed: keep what we have
            if not chunk:
                empty += 1
                if empty >= 2:
                    break                  # two empty windows: before the listing
            else:
                empty = 0
                for row in chunk:
                    rows[row[0]] = row
            if len(rows) >= bars:
                break
            end -= window
        return [rows[key] for key in sorted(rows)][-bars:]

    async def _window(self, symbol: str, match, interval: str, start: datetime,
                      end: datetime) -> Optional[List[List[float]]]:
        """One history request. None when it failed, [] when it had no data."""
        params = {
            "exchange": match.exchange,
            "symboltoken": match.token,
            "interval": interval,
            "fromdate": start.strftime("%Y-%m-%d %H:%M"),
            "todate": end.strftime("%Y-%m-%d %H:%M"),
        }
        try:
            client = await asyncio.to_thread(angel_auth.ensure)
            response = await asyncio.to_thread(client.getCandleData, params)
        except Exception as exc:  # noqa: BLE001 - normalise every SDK/network failure
            self._last_error = "Angel One history request failed: {}".format(exc)
            log.warning(self._last_error)
            return None

        if not response or not response.get("status"):
            self._last_error = (response or {}).get("message", "no data returned")
            log.debug("Angel One history for %s: %s", symbol, self._last_error)
            return None

        rows: List[List[float]] = []
        for candle in response.get("data") or []:
            try:
                ts = datetime.fromisoformat(candle[0]).timestamp()
                rows.append([ts, float(candle[1]), float(candle[2]),
                           float(candle[3]), float(candle[4]), float(candle[5])])
            except (ValueError, IndexError, TypeError):
                continue  # one malformed row must not drop the whole series
        return rows

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
