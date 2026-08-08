"""Twelve Data provider — real FX + XAUUSD (requires TWELVEDATA_API_KEY).

The free tier has no websocket, so `stream` polls the quote endpoint on a
credit-aware interval and synthesises ticks from it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Sequence

import httpx

from ...config import settings
from ...instruments import get_instrument
from .base import MarketDataProvider, ProviderTick, TickHandler

log = logging.getLogger("meridian.twelvedata")

REST = "https://api.twelvedata.com"

SYMBOL_MAP: Dict[str, str] = {
    "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
    "AUDUSD": "AUD/USD", "USDCAD": "USD/CAD", "XAUUSD": "XAU/USD",
    "BTCUSD": "BTC/USD", "ETHUSD": "ETH/USD",
}
INTERVALS: Dict[str, str] = {
    "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "4h": "4h", "1d": "1day", "1w": "1week",
}


class TwelveDataProvider(MarketDataProvider):
    name = "twelvedata"
    live = True

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=15.0)
        self._key = settings.twelvedata_api_key or ""

    def supports(self, symbol: str) -> bool:
        return bool(self._key) and symbol.upper() in SYMBOL_MAP

    async def close(self) -> None:
        await self._client.aclose()

    async def history(self, symbol: str, timeframe: str, bars: int) -> List[List[float]]:
        pair = SYMBOL_MAP.get(symbol.upper())
        interval = INTERVALS.get(timeframe.lower())
        if not pair or not interval or not self._key:
            return []
        params = {
            "symbol": pair, "interval": interval,
            "outputsize": str(min(bars, 5000)), "apikey": self._key,
            "format": "JSON", "timezone": "UTC",
        }
        try:
            resp = await self._client.get(REST + "/time_series", params=params)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("twelvedata history failed for %s: %s", symbol, exc)
            return []

        if payload.get("status") == "error":
            log.warning("twelvedata error for %s: %s", symbol, payload.get("message"))
            return []

        rows: List[List[float]] = []
        for entry in reversed(payload.get("values", []) or []):
            try:
                dt = datetime.strptime(entry["datetime"], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    dt = datetime.strptime(entry["datetime"], "%Y-%m-%d")
                except ValueError:
                    continue
            rows.append([
                dt.replace(tzinfo=timezone.utc).timestamp(),
                float(entry["open"]), float(entry["high"]),
                float(entry["low"]), float(entry["close"]),
                float(entry.get("volume") or 0.0),
            ])
        return rows

    async def stream(self, symbols: Sequence[str], handler: TickHandler) -> None:
        targets = [s for s in symbols if self.supports(s)]
        if not targets:
            return
        # 8 credits/min on the free tier — one batched quote call every 8s is safe.
        interval = max(8.0, len(targets) * 1.5)
        while True:
            pairs = ",".join(SYMBOL_MAP[s.upper()] for s in targets)
            try:
                resp = await self._client.get(
                    REST + "/price", params={"symbol": pairs, "apikey": self._key}
                )
                resp.raise_for_status()
                data = resp.json()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.debug("twelvedata poll failed: %s", exc)
                await asyncio.sleep(interval)
                continue

            now = time.time()
            for sym in targets:
                pair = SYMBOL_MAP[sym.upper()]
                node = data.get(pair) if isinstance(data, dict) and pair in data else data
                try:
                    price = float(node["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                inst = get_instrument(sym)
                half = inst.from_pips(inst.typical_spread_pips) / 2
                await handler(ProviderTick(
                    symbol=sym, time=now, price=price,
                    bid=round(price - half, inst.digits),
                    ask=round(price + half, inst.digits),
                    volume=0.0,
                ))
            await asyncio.sleep(interval)
