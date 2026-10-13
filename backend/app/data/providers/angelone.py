"""Angel One SmartAPI market data — NSE/BSE/NFO/CDS/MCX prices from the same
broker connection that places the orders. It is the only market-data source
there is: it needs no subscription of its own, it only exists while that
session is connected, and nothing stands in for it when it has no data.

**Polling, not a websocket.** Angel One's SmartWebSocketV2 would give
tighter latency, but it needs its own reconnect/threading machinery on top
of what TRADING.md §2a already built. Polling `getMarketData` (a single
batched request for every symbol at once) every few seconds is simpler and
well within Angel One's REST rate limits for a normal watchlist. The number
on screen is Angel One's actual last-traded price, refreshed every few
seconds instead of every tick.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from ...broker.angelone.auth import angel_auth, is_auth_failure
from ...broker.angelone.instruments import InstrumentUnsupported, angel_instruments
from ...broker.angelone.orders import IST
from ...instruments import get_instrument, get_timeframe
from ..sessions import BarGrid, bar_grid
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

#: Segments whose candles count volume in contracts while the quote's running
#: total counts units. Measured on NIFTY27OCT26FUT (lot 65) on 13 October
#: 2026: the history's 15-minute bars held 5,085 and 7,872, and the two bars
#: built from quotes straight after them 79,930 and 63,830 — every live bar
#: an apparent volume spike. Only NFO is listed because only NFO was
#: measured; MCX and CDS are not known either way.
VOLUME_IN_LOTS_EXCHANGES = frozenset({"NFO"})

#: Half the spread assumed, as a fraction of price, for a quote that comes
#: with no order book — an index, or any market outside its hours. Not zero:
#: a spread of nothing would make every trade look free to the cost model.
ASSUMED_HALF_SPREAD = 0.00025


class AngelOneMarketDataProvider(MarketDataProvider):
    name = "angelone"
    live = True

    def __init__(self) -> None:
        self._last_error: Optional[str] = None
        #: The day's traded volume as last reported, per symbol.
        self._day_volume: Dict[str, float] = {}

    async def prepare(self) -> None:
        """Load the scrip master, which is what says which stocks exist."""
        await angel_instruments.ensure_loaded()

    def supports(self, symbol: str) -> bool:
        try:
            instrument = get_instrument(symbol)
        except KeyError:
            return False
        return instrument.tradable_in_india and angel_auth.connected

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def bar_grid(self, symbol: str) -> Optional[BarGrid]:
        """Angel One stamps a bar by its exchange's session: intraday bars
        from the open (09:15, 10:15 … on the NSE), a daily bar at local
        midnight. Bars built from its quotes are opened on the same grid."""
        try:
            return bar_grid(get_instrument(symbol).asset_class)
        except KeyError:
            return None

    # ── history ───────────────────────────────────────────────

    async def history(self, symbol: str, timeframe: str, bars: int) -> List[List[float]]:
        timeframe = timeframe.lower()
        if timeframe in DERIVED:
            source = DERIVED[timeframe]
            target = get_timeframe(timeframe).seconds
            factor = target // get_timeframe(source).seconds
            fine = await self.history(symbol, source, bars * factor)
            return resample_rows(fine, target, self.bar_grid(symbol))[-bars:]

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
            self._drop_dead_session(response)
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
        units_per_bar_unit: Dict[str, float] = {}
        for symbol in symbols:
            try:
                match = await angel_instruments.resolve(symbol)
            except InstrumentUnsupported:
                continue
            by_exchange.setdefault(match.exchange, []).append(match.token)
            token_to_symbol[match.token] = symbol
            lot = float(getattr(match, "lot_size", 1) or 1)
            units_per_bar_unit[symbol] = lot if match.exchange in VOLUME_IN_LOTS_EXCHANGES else 1.0

        if not by_exchange:
            return

        client = await asyncio.to_thread(angel_auth.ensure)
        response = await asyncio.to_thread(client.getMarketData, "FULL", by_exchange)
        if not response or not response.get("status"):
            self._last_error = (response or {}).get("message", "no data returned")
            self._drop_dead_session(response)
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
            bid, ask = self._best_bid_ask(row, ltp)
            # In the unit the venue's own candles count in, so a bar built
            # from quotes can be read beside the ones before it.
            traded = self._traded_since_last_poll(symbol, row.get("tradeVolume"))
            await handler(ProviderTick(
                symbol=symbol, time=time.time(), price=ltp, bid=bid, ask=ask,
                volume=traded / units_per_bar_unit.get(symbol, 1.0),
            ))

    @staticmethod
    def _drop_dead_session(response: object) -> None:
        """A token the broker calls invalid is not used again: the session
        is dropped, and the next request logs in (after whatever wait the
        last failure left). Without this the poll went on presenting it."""
        if is_auth_failure(response):
            angel_auth.invalidate("Angel One no longer accepts the session token ({})."
                                  .format((response or {}).get("message") or "no reason given"))

    @staticmethod
    def _best_bid_ask(row: dict, ltp: float) -> Tuple[float, float]:
        """The top of Angel One's own order book. Where there is none to
        read, a spread is assumed around the last trade and never left at
        zero."""
        depth = row.get("depth") or {}
        try:
            bid = float((depth.get("buy") or [{}])[0].get("price") or 0.0)
            ask = float((depth.get("sell") or [{}])[0].get("price") or 0.0)
        except (TypeError, ValueError, AttributeError, IndexError):
            bid = ask = 0.0
        if 0.0 < bid < ask:
            return bid, ask
        return ltp * (1.0 - ASSUMED_HALF_SPREAD), ltp * (1.0 + ASSUMED_HALF_SPREAD)

    def _traded_since_last_poll(self, symbol: str, day_volume: object) -> float:
        """`tradeVolume` is the day's running total, and the candle store adds
        each tick's volume to the forming bar — passed through as it comes,
        every poll would add the whole day again. What a tick carries is the
        increase since the last one: nothing on the first sighting, and the
        new total when the count has restarted for a new session."""
        try:
            total = float(day_volume or 0.0)
        except (TypeError, ValueError):
            return 0.0
        previous = self._day_volume.get(symbol)
        self._day_volume[symbol] = total
        if previous is None:
            return 0.0
        return total - previous if total >= previous else total
