"""CoinDCX provider — live crypto, no API key required.

CoinDCX's public market-data endpoints are open, so this provider streams real
prices without the trader having to create keys. Keys are only needed to place
orders, which Meridian deliberately never does.

Two quirks the rest of the codebase should not have to know about:

* candles come back **newest-first** with millisecond timestamps, so they are
  reversed and rescaled to seconds here;
* there is no public websocket for market data on the documented REST surface,
  so quotes are polled from the ticker snapshot. At the 1 Hz the fast tier
  needs, a poll is indistinguishable from a stream — and one request covers
  every symbol at once rather than one socket per pair.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional, Sequence

import httpx

from ...instruments import get_timeframe
from .base import MarketDataProvider, ProviderTick, TickHandler

log = logging.getLogger("meridian.coindcx")

PUBLIC = "https://public.coindcx.com"
API = "https://api.coindcx.com"

#: Meridian symbol → (candle pair, ticker market).
#:
#: CoinDCX uses two different identifiers for the same instrument: the candles
#: endpoint wants the `B-BTC_USDT` "ecode-prefixed" pair while the ticker
#: returns the flat `BTCUSDT` market. Keeping both in one map is what stops
#: that difference leaking outward.
PAIRS: Dict[str, tuple] = {
    # USDT-quoted (the deep books)
    "BTCUSD": ("B-BTC_USDT", "BTCUSDT"),
    "ETHUSD": ("B-ETH_USDT", "ETHUSDT"),
    "SOLUSD": ("B-SOL_USDT", "SOLUSDT"),
    "XRPUSD": ("B-XRP_USDT", "XRPUSDT"),
    "DOGEUSD": ("B-DOGE_USDT", "DOGEUSDT"),
    "BNBUSD": ("B-BNB_USDT", "BNBUSDT"),
    "ADAUSD": ("B-ADA_USDT", "ADAUSDT"),
    "AVAXUSD": ("B-AVAX_USDT", "AVAXUSDT"),
    "LINKUSD": ("B-LINK_USDT", "LINKUSDT"),
    "DOTUSD": ("B-DOT_USDT", "DOTUSDT"),
    "LTCUSD": ("B-LTC_USDT", "LTCUSDT"),
    "TRXUSD": ("B-TRX_USDT", "TRXUSDT"),
    # INR-quoted — what an Indian resident actually settles in
    "BTCINRC": ("I-BTC_INR", "BTCINR"),
    "ETHINRC": ("I-ETH_INR", "ETHINR"),
    "SOLINRC": ("I-SOL_INR", "SOLINR"),
    "XRPINRC": ("I-XRP_INR", "XRPINR"),
    "DOGEINRC": ("I-DOGE_INR", "DOGEINR"),
}

#: The candles endpoint accepts exactly these four and 422s on everything
#: else — probed against the live API, not taken from the docs, which list
#: many more. The map previously claimed 5m/30m/4h/1w and every request for
#: them failed silently, quietly hollowing out the multi-timeframe ladder.
NATIVE_INTERVALS: Dict[str, str] = {
    "1m": "1m", "15m": "15m", "1h": "1h", "1d": "1d",
}

#: Everything else is built by aggregating a finer native interval.
#: (target timeframe) -> (source interval, candles of source per target)
DERIVED_INTERVALS: Dict[str, tuple] = {
    "5m": ("1m", 5),
    "30m": ("15m", 2),
    "4h": ("1h", 4),
    "1w": ("1d", 7),
}

INTERVALS: Dict[str, str] = dict(NATIVE_INTERVALS)

#: One ticker call serves every subscribed symbol, so polling faster than this
#: buys nothing but rate-limit risk.
POLL_SECONDS = 1.0


class CoinDCXProvider(MarketDataProvider):
    name = "coindcx"
    live = True

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=12.0, headers={
            "accept": "application/json",
        })

    def supports(self, symbol: str) -> bool:
        return symbol.upper() in PAIRS

    @staticmethod
    def supports_timeframe(timeframe: str) -> bool:
        timeframe = timeframe.lower()
        return timeframe in NATIVE_INTERVALS or timeframe in DERIVED_INTERVALS

    async def close(self) -> None:
        await self._client.aclose()

    # ── history ───────────────────────────────────────────────

    async def history(self, symbol: str, timeframe: str, bars: int) -> List[List[float]]:
        timeframe = timeframe.lower()
        entry = PAIRS.get(symbol.upper())
        if not entry:
            return []

        if timeframe in DERIVED_INTERVALS:
            source, factor = DERIVED_INTERVALS[timeframe]
            fine = await self.history(symbol, source, min(bars * factor, 1000))
            if not fine:
                return []
            return _resample(fine, get_timeframe(timeframe).seconds)[-bars:]

        interval = NATIVE_INTERVALS.get(timeframe)
        if not interval:
            return []
        pair = entry[0]

        try:
            resp = await self._client.get(
                PUBLIC + "/market_data/candles",
                params={"pair": pair, "interval": interval,
                        "limit": min(max(bars, 100), 1000)},
            )
            resp.raise_for_status()
            chunk = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("coindcx history failed for %s %s: %s", symbol, timeframe, exc)
            return []

        if not isinstance(chunk, list) or not chunk:
            return []

        rows = [
            [float(c["time"]) / 1000.0, float(c["open"]), float(c["high"]),
             float(c["low"]), float(c["close"]), float(c.get("volume") or 0.0)]
            for c in chunk
            if c.get("time") is not None
        ]
        # Newest-first from the API; the store wants oldest-first.
        rows.sort(key=lambda r: r[0])
        return rows

    # ── quotes ────────────────────────────────────────────────

    async def _ticker(self) -> Dict[str, dict]:
        resp = await self._client.get(API + "/exchange/ticker")
        resp.raise_for_status()
        return {row["market"]: row for row in resp.json() if row.get("market")}

    async def stream(self, symbols: Sequence[str], handler: TickHandler) -> None:
        wanted = {s.upper(): PAIRS[s.upper()][1]
                  for s in symbols if self.supports(s)}
        if not wanted:
            return

        failures = 0
        while True:
            try:
                snapshot = await self._ticker()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                failures += 1
                log.warning("coindcx ticker poll failed (%d): %s", failures, exc)
                # Back off on a sustained outage rather than hammering a
                # service that is already struggling.
                await asyncio.sleep(min(POLL_SECONDS * 2 ** failures, 30.0))
                continue

            now = time.time()
            for symbol, market in wanted.items():
                row = snapshot.get(market)
                if not row:
                    continue
                price = _to_float(row.get("last_price"))
                if price is None or price <= 0:
                    continue
                await handler(ProviderTick(
                    symbol=symbol,
                    time=now,
                    price=price,
                    bid=_to_float(row.get("bid")) or price,
                    ask=_to_float(row.get("ask")) or price,
                    # Deliberately zero. The ticker's `volume` field is the
                    # rolling **24-hour** total, and the store accumulates tick
                    # volume into the forming bar — feeding it in would add a
                    # day's volume every second and flatten the entire
                    # histogram behind one impossible bar. The candles endpoint
                    # reports true partial-bar volume and is re-read on every
                    # analysis, so that stays the authority.
                    volume=0.0,
                ))

            await asyncio.sleep(POLL_SECONDS)


def _to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resample(rows: List[List[float]], tf_seconds: int) -> List[List[float]]:
    """Aggregate finer candles into coarser buckets.

    Rows are [time, open, high, low, close, volume] ascending. Bucket start is
    floor-aligned to the target timeframe so a 4h bar always begins on a 4h
    boundary rather than on whatever the first row happened to be.
    """
    if not rows or tf_seconds <= 0:
        return rows

    out: List[List[float]] = []
    bucket_start = None
    current: Optional[List[float]] = None

    for time_s, open_p, high, low, close, volume in rows:
        start = int(time_s // tf_seconds) * tf_seconds
        if bucket_start is None or start != bucket_start:
            if current is not None:
                out.append(current)
            bucket_start = start
            current = [float(start), open_p, high, low, close, volume]
            continue
        current[2] = max(current[2], high)
        current[3] = min(current[3], low)
        current[4] = close
        current[5] += volume

    if current is not None:
        out.append(current)
    return out
