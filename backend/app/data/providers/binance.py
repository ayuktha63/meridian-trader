"""Binance provider — real, keyless BTCUSD / ETHUSD data."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Dict, List, Sequence

import httpx

from ...instruments import get_instrument
from .base import MarketDataProvider, ProviderTick, TickHandler

log = logging.getLogger("meridian.binance")

REST = "https://api.binance.com/api/v3"
WS = "wss://stream.binance.com:9443/stream?streams="

SYMBOL_MAP: Dict[str, str] = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}
INTERVALS: Dict[str, str] = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w",
}


class BinanceProvider(MarketDataProvider):
    name = "binance"
    live = True

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=12.0)

    def supports(self, symbol: str) -> bool:
        return symbol.upper() in SYMBOL_MAP

    async def close(self) -> None:
        await self._client.aclose()

    async def history(self, symbol: str, timeframe: str, bars: int) -> List[List[float]]:
        pair = SYMBOL_MAP.get(symbol.upper())
        interval = INTERVALS.get(timeframe.lower())
        if not pair or not interval:
            return []
        rows: List[List[float]] = []
        remaining = min(bars, 6000)
        end_time = None
        while remaining > 0:
            limit = min(1000, remaining)
            params = {"symbol": pair, "interval": interval, "limit": limit}
            if end_time:
                params["endTime"] = end_time
            try:
                resp = await self._client.get(REST + "/klines", params=params)
                resp.raise_for_status()
                chunk = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("binance history failed for %s %s: %s", symbol, timeframe, exc)
                break
            if not chunk:
                break
            rows = [
                [float(k[0]) / 1000.0, float(k[1]), float(k[2]),
                 float(k[3]), float(k[4]), float(k[5])]
                for k in chunk
            ] + rows
            remaining -= len(chunk)
            end_time = int(chunk[0][0]) - 1
            if len(chunk) < limit:
                break
        return rows

    async def stream(self, symbols: Sequence[str], handler: TickHandler) -> None:
        pairs = [SYMBOL_MAP[s.upper()] for s in symbols if self.supports(s)]
        if not pairs:
            return
        reverse = {v: k for k, v in SYMBOL_MAP.items()}
        url = WS + "/".join(p.lower() + "@trade" for p in pairs)

        import websockets

        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, close_timeout=5) as ws:
                    log.info("binance stream connected: %s", ",".join(pairs))
                    backoff = 1.0
                    async for raw in ws:
                        try:
                            msg = json.loads(raw).get("data", {})
                            pair = msg.get("s")
                            price = float(msg.get("p", 0))
                        except (ValueError, TypeError):
                            continue
                        symbol = reverse.get(pair)
                        if not symbol or price <= 0:
                            continue
                        inst = get_instrument(symbol)
                        half = inst.from_pips(inst.typical_spread_pips) / 2
                        await handler(ProviderTick(
                            symbol=symbol,
                            time=float(msg.get("T", time.time() * 1000)) / 1000.0,
                            price=price,
                            bid=round(price - half, inst.digits),
                            ask=round(price + half, inst.digits),
                            volume=float(msg.get("q", 0) or 0),
                        ))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("binance stream dropped (%s); reconnecting in %.0fs", exc, backoff)
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
