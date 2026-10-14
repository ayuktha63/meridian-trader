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

#: A quote older than this is not a price to act on. The poll is every three
#: seconds, so this is six of them missed — the feed has stopped, the session
#: has gone, or the market is shut.
STALE_QUOTE_SECONDS = 20.0

#: A series its source could not supply is asked for again this often — not
#: on every request, which would hammer a venue that is already struggling.
SOURCE_RETRY_SECONDS = 60.0


class MarketFeed:
    def __init__(self) -> None:
        self.providers: Dict[str, MarketDataProvider] = {}
        self.quotes: Dict[str, QuoteSnapshot] = {}
        self._tasks: List[asyncio.Task] = []
        self._subscribers: Set[asyncio.Queue] = set()
        self._watch_counts: Dict[Tuple[str, str], int] = defaultdict(int)
        self._hydrate_locks: Dict[Tuple[str, str], asyncio.Lock] = {}
        #: Series whose provider returned less than was asked for — as deep
        #: as that source goes.
        self._depth_reached: Set[Tuple[str, str]] = set()
        #: When the source for a series last failed to supply it.
        self._source_failed: Dict[Tuple[str, str], float] = {}
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
        for provider in self.providers.values():
            try:
                await provider.prepare()
            except Exception as exc:  # noqa: BLE001 - it serves what it can without
                log.warning("%s could not load its reference data: %s", provider.name, exc)
        # Only the default set is streamed; every other stock hydrates on
        # demand when the trader actually selects one. Read after `prepare`,
        # which is what says which stocks there are.
        symbols = symbols or list(DEFAULT_STREAM_SYMBOLS)

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
        """Rebuild providers and restart streaming after the broker session
        changes. The candle store is cleared: bars held from before are not
        kept under a connection that did not supply them."""
        log.info("reloading market feed (%s)", reason)
        await self.stop()
        store.clear()
        self.quotes.clear()
        self._day_open.clear()
        self._hydrate_locks.clear()
        self._depth_reached.clear()
        self._source_failed.clear()
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
        async def handler(tick: ProviderTick) -> None:
            await self._on_tick(tick, provider.name)

        while self._running:
            try:
                await provider.stream(symbols, handler)
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
            wanted = min(bars or settings.history_bars, series.maxlen)
            have = len(series)
            # A chart is content with a couple of hundred bars. A caller that
            # names a depth — a backtest — gets it, once: if the provider has
            # already shown it cannot go that deep, asking again on every
            # call would only hammer it for the same answer.
            enough = have >= min(wanted, 200)
            deeper = bars is not None and have < wanted and key not in self._depth_reached
            if enough and not deeper:
                return
            provider = resolve_provider(symbol, self.providers or build_providers())
            if provider is None:
                return          # nothing serves it: the series stays empty
            if time.time() - self._source_failed.get(key, 0.0) < SOURCE_RETRY_SECONDS:
                return
            rows = await provider.history(symbol, timeframe, wanted)
            if not rows:
                # No bars are drawn in their place. The chart is empty until
                # the venue answers, and it is asked again shortly.
                self._source_failed[key] = time.time()
                log.warning("%s returned no history for %s %s", provider.name,
                            symbol, timeframe)
                return
            self._source_failed.pop(key, None)
            if len(rows) < wanted:
                self._depth_reached.add(key)
            if len(rows) > have:
                series.replace(rows, source=provider.name, grid=provider.bar_grid(symbol))
                self._seed_quote(symbol, series.last_close or 0.0,
                                 float(series.last_time or 0))

    def _seed_quote(self, symbol: str, price: float, seen_at: float = 0.0) -> None:
        """A first quote from history's last close, until a real one arrives.
        Stamped with the bar it was read from, not with now: a close taken
        from history is not a quote that has just come in, and reads as
        stale until one does."""
        if symbol in self.quotes or price <= 0:
            return
        inst = get_instrument(symbol)
        half = inst.from_pips(inst.typical_spread_pips) / 2
        self.quotes[symbol] = QuoteSnapshot(
            symbol=symbol, bid=price - half, ask=price + half, last=price,
            spread_pips=inst.typical_spread_pips, updated_at=seen_at,
            live=self.is_live(symbol),
        )

    # ── tick handling ─────────────────────────────────────────

    async def _on_tick(self, tick: ProviderTick, source: Optional[str] = None) -> None:
        symbol = tick.symbol.upper()
        inst = get_instrument(symbol)

        closed_bars: List[Tuple[str, Dict[str, float]]] = []
        updated: List[Tuple[str, Dict[str, float]]] = []

        for tf in TIMEFRAME_CODES:
            series = store.series(symbol, tf)
            if len(series) == 0:
                continue
            if source is not None and series.source not in (None, source):
                continue        # one venue's quote is never folded into another's bars
            opened_new, bar = series.apply_price(tick.time, tick.price, tick.volume)
            payload = {
                "time": int(bar[0]), "open": bar[1], "high": bar[2],
                "low": bar[3], "close": bar[4], "volume": bar[5],
            }
            if self._watch_counts.get((symbol, tf), 0) > 0:
                updated.append((tf, payload))
                if opened_new:
                    closed_bars.append((tf, payload))

        day_series = store.series(symbol, "1d")
        same_venue = len(day_series) > 0 and (
            source is None or day_series.source in (None, source))
        day_open = self._day_open_price(symbol) if same_venue else 0.0
        change_abs = tick.price - day_open if day_open else 0.0
        change_pct = (change_abs / day_open * 100.0) if day_open else 0.0
        day_high = day_series.rows(1)[0][2] if same_venue else tick.price
        day_low = day_series.rows(1)[0][3] if same_venue else tick.price

        quote = QuoteSnapshot(
            symbol=symbol,
            bid=tick.bid, ask=tick.ask, last=tick.price,
            spread_pips=round(inst.to_pips(tick.ask - tick.bid), 2),
            change_abs=round(change_abs, inst.digits),
            change_pct=round(change_pct, 3),
            day_high=day_high, day_low=day_low,
            updated_at=tick.time,
            live=self.is_live(symbol),
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
        """The last quote, with how old it is as it is read."""
        quote = self.quotes.get(symbol.upper())
        if quote is None:
            return None
        age = max(0.0, time.time() - quote.updated_at)
        return quote.model_copy(update={
            "age_seconds": round(age, 1), "stale": age > STALE_QUOTE_SECONDS})

    def fresh_quote(self, symbol: str) -> Optional[QuoteSnapshot]:
        """The last quote if it is recent enough to act on, else None. What a
        simulated fill is made at: a fill at a price from before the feed
        stopped is not a fill anything could have got."""
        quote = self.quote(symbol)
        return quote if quote is not None and not quote.stale else None

    def price(self, symbol: str) -> Optional[float]:
        quote = self.quote(symbol)
        if quote:
            return quote.last
        series = store.series(symbol, "1m")
        return series.last_close

    def provider_for(self, symbol: str) -> str:
        """The provider that serves this symbol, or "" when nothing does."""
        provider = resolve_provider(symbol, self.providers)
        return provider.name if provider is not None else ""

    def is_live(self, symbol: str) -> bool:
        provider = resolve_provider(symbol, self.providers)
        return provider is not None and provider.live

    def unsupplied(self) -> List[str]:
        """Series a source was asked for and could not supply, and so are
        empty: "SYMBOL:timeframe"."""
        return sorted("{}:{}".format(symbol, timeframe)
                      for symbol, timeframe in self._source_failed
                      if not len(store.series(symbol, timeframe)))

    def source_of(self, symbol: str, timeframe: str) -> Optional[str]:
        """Where the bars held for this series came from, or None if nothing
        has been loaded."""
        if not store.has(symbol, timeframe):
            return None
        return store.series(symbol, timeframe).source

    def status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "providers": sorted(self.providers.keys()),
            "subscribers": len(self._subscribers),
            "watched": {"{}:{}".format(s, t): c
                        for (s, t), c in self._watch_counts.items() if c > 0},
            "series": {"{}:{}".format(s, t): len(store.series(s, t))
                       for (s, t) in store.keys()},
            "stale_quotes": sorted(symbol for symbol in self.quotes
                                   if (self.quote(symbol) or QuoteSnapshot(
                                       symbol=symbol, bid=0, ask=0, last=0,
                                       spread_pips=0)).stale),
        }


feed = MarketFeed()
