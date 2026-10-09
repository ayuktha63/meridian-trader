"""Market feed: providers → ticks → candles on every timeframe → subscribers.

A tick is folded into the forming bar of *all* timeframes at once, so 1m and
4H stay consistent with each other and with the live quote. Bar-close events
are published separately because they are what triggers auto re-analysis.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from ..config import settings
from ..instruments import DEFAULT_STREAM_SYMBOLS, TIMEFRAME_CODES, get_instrument
from ..schemas.market import QuoteSnapshot
from .providers import build_providers, resolve_provider
from .providers.base import MarketDataProvider, ProviderTick
from .providers.registry import provider_assignments
from .store import store

log = logging.getLogger("meridian.feed")

#: Quote fan-out rate. The live position tier rides on these, so this is
#: also the resolution of the exit alerts.
QUOTE_PUBLISH_HZ = 4.0


class MarketFeed:
    def __init__(self) -> None:
        self.providers: Dict[str, MarketDataProvider] = {}
        self.quotes: Dict[str, QuoteSnapshot] = {}
        self._tasks: List[asyncio.Task] = []
        self._subscribers: Set[asyncio.Queue] = set()
        self._watch_counts: Dict[Tuple[str, str], int] = defaultdict(int)
        self._hydrate_locks: Dict[Tuple[str, str], asyncio.Lock] = {}
        self._last_quote_publish: Dict[str, float] = {}
        self._day_open: Dict[str, float] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False

    # ── lifecycle ─────────────────────────────────────────────

    async def start(self, symbols: Optional[List[str]] = None) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self.providers = build_providers()
        # Only the default set is streamed at boot; the ~130 stocks
        # hydrate on demand when the trader actually selects one.
        symbols = symbols or DEFAULT_STREAM_SYMBOLS

        assignments = provider_assignments(symbols, self.providers)
        log.info("provider assignments: %s", assignments)

        # Warm the default view so the first request is instant; the rest
        # hydrate lazily on demand.
        await self.ensure_history(symbols[0], "15m")
        self._tasks.append(asyncio.create_task(self._warm_up(symbols)))

        for provider_name, provider_symbols in assignments.items():
            provider = self.providers[provider_name]
            self._tasks.append(
                asyncio.create_task(
                    self._run_stream(provider, provider_symbols),
                    name="feed-{}".format(provider_name),
                )
            )

    async def reload(self, reason: str = "settings changed") -> None:
        """Rebuild providers and restart streaming after a settings change.

        The candle store is cleared because bars from the simulator must never
        be blended with real ones — a mixed series would silently corrupt every
        downstream indicator.
        """
        log.info("reloading market feed (%s)", reason)
        await self.stop()
        store.clear()
        self.quotes.clear()
        self._day_open.clear()
        self._hydrate_locks.clear()
        await self.start()

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()
        for provider in self.providers.values():
            try:
                await provider.close()
            except Exception:  # noqa: BLE001
                pass

    async def _warm_up(self, symbols: List[str]) -> None:
        """Background hydration of the timeframes the MTF engine always needs.

        Only the first few symbols are warmed eagerly: broker APIs are rate
        limited, and everything else hydrates on demand the moment a chart
        asks for it.
        """
        for symbol in symbols[:3]:
            for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
                try:
                    await self.ensure_history(symbol, tf)
                except Exception as exc:  # noqa: BLE001
                    log.debug("warm-up failed %s %s: %s", symbol, tf, exc)
                await asyncio.sleep(0)

    async def _run_stream(self, provider: MarketDataProvider, symbols: List[str]) -> None:
        while self._running:
            try:
                await provider.stream(symbols, self._on_tick)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("%s stream error: %s — restarting in 5s", provider.name, exc)
                await asyncio.sleep(5)

    # ── history ───────────────────────────────────────────────

    async def ensure_history(self, symbol: str, timeframe: str, bars: Optional[int] = None) -> None:
        symbol = symbol.upper()
        timeframe = timeframe.lower()
        key = (symbol, timeframe)
        lock = self._hydrate_locks.setdefault(key, asyncio.Lock())
        async with lock:
            series = store.series(symbol, timeframe)
            wanted = bars or settings.history_bars
            if len(series) >= min(wanted, 200):
                return
            provider = resolve_provider(symbol, self.providers or build_providers())
            rows = await provider.history(symbol, timeframe, wanted)
            if not rows and provider.name != "synthetic":
                log.info("%s returned no history for %s %s; using synthetic",
                         provider.name, symbol, timeframe)
                rows = await self.providers["synthetic"].history(symbol, timeframe, wanted)
            if rows:
                series.replace(rows)
                self._seed_quote(symbol, series.last_close or 0.0)

    def _seed_quote(self, symbol: str, price: float) -> None:
        if symbol in self.quotes or price <= 0:
            return
        inst = get_instrument(symbol)
        half = inst.from_pips(inst.typical_spread_pips) / 2
        self.quotes[symbol] = QuoteSnapshot(
            symbol=symbol, bid=price - half, ask=price + half, last=price,
            spread_pips=inst.typical_spread_pips, updated_at=time.time(),
            live=resolve_provider(symbol, self.providers).live if self.providers else False,
        )

    # ── tick handling ─────────────────────────────────────────

    async def _on_tick(self, tick: ProviderTick) -> None:
        symbol = tick.symbol.upper()
        inst = get_instrument(symbol)

        closed_bars: List[Tuple[str, Dict[str, float]]] = []
        updated: List[Tuple[str, Dict[str, float]]] = []

        for tf in TIMEFRAME_CODES:
            series = store.series(symbol, tf)
            if len(series) == 0:
                continue
            opened_new, bar = series.apply_price(tick.time, tick.price, tick.volume)
            payload = {
                "time": int(bar[0]), "open": bar[1], "high": bar[2],
                "low": bar[3], "close": bar[4], "volume": bar[5],
            }
            if self._watch_counts.get((symbol, tf), 0) > 0:
                updated.append((tf, payload))
                if opened_new:
                    closed_bars.append((tf, payload))

        day_open = self._day_open_price(symbol)
        change_abs = tick.price - day_open if day_open else 0.0
        change_pct = (change_abs / day_open * 100.0) if day_open else 0.0
        day_series = store.series(symbol, "1d")
        day_high = day_series.rows(1)[0][2] if len(day_series) else tick.price
        day_low = day_series.rows(1)[0][3] if len(day_series) else tick.price

        quote = QuoteSnapshot(
            symbol=symbol,
            bid=tick.bid, ask=tick.ask, last=tick.price,
            spread_pips=round(inst.to_pips(tick.ask - tick.bid), 2),
            change_abs=round(change_abs, inst.digits),
            change_pct=round(change_pct, 3),
            day_high=day_high, day_low=day_low,
            updated_at=tick.time,
            live=resolve_provider(symbol, self.providers).live,
        )
        self.quotes[symbol] = quote

        now = time.monotonic()
        if now - self._last_quote_publish.get(symbol, 0.0) >= 1.0 / QUOTE_PUBLISH_HZ:
            self._last_quote_publish[symbol] = now
            self._publish({"type": "quote", "symbol": symbol, "data": quote.model_dump()})
            for tf, payload in updated:
                self._publish({
                    "type": "candle", "symbol": symbol,
                    "timeframe": tf, "candle": payload,
                })

        for tf, payload in closed_bars:
            self._publish({
                "type": "bar_closed", "symbol": symbol,
                "timeframe": tf, "candle": payload,
            })

    def _day_open_price(self, symbol: str) -> float:
        cached = self._day_open.get(symbol)
        today = int(time.time() // 86400)
        if cached and self._day_open.get(symbol + ":day") == today:
            return cached
        series = store.series(symbol, "1d")
        if len(series):
            price = series.rows(1)[0][1]
            self._day_open[symbol] = price
            self._day_open[symbol + ":day"] = today
            return price
        return 0.0

    # ── pub/sub ───────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def watch(self, symbol: str, timeframe: str) -> None:
        self._watch_counts[(symbol.upper(), timeframe.lower())] += 1

    def unwatch(self, symbol: str, timeframe: str) -> None:
        key = (symbol.upper(), timeframe.lower())
        if self._watch_counts.get(key):
            self._watch_counts[key] -= 1

    def _publish(self, event: Dict[str, Any]) -> None:
        dead = []
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer: drop the oldest event rather than the socket.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except Exception:  # noqa: BLE001
                    dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)

    # ── accessors ─────────────────────────────────────────────

    def quote(self, symbol: str) -> Optional[QuoteSnapshot]:
        return self.quotes.get(symbol.upper())

    def price(self, symbol: str) -> Optional[float]:
        quote = self.quote(symbol)
        if quote:
            return quote.last
        series = store.series(symbol, "1m")
        return series.last_close

    def provider_for(self, symbol: str) -> str:
        if not self.providers:
            return "synthetic"
        return resolve_provider(symbol, self.providers).name

    def status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "providers": sorted(self.providers.keys()),
            "mode": settings.market_provider,
            "subscribers": len(self._subscribers),
            "watched": {"{}:{}".format(s, t): c
                        for (s, t), c in self._watch_counts.items() if c > 0},
            "series": {"{}:{}".format(s, t): len(store.series(s, t))
                       for (s, t) in store.keys()},
        }


feed = MarketFeed()
