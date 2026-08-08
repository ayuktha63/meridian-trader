"""Zerodha Kite Connect provider — live NSE / BSE / MCX / CDS data.

Two things make this provider different from the others:

* **kiteconnect is synchronous** (requests + a Twisted websocket). Every REST
  call runs in a thread executor, and the ticker's callbacks are marshalled
  back onto the event loop with `call_soon_threadsafe` — touching asyncio
  state directly from the Twisted thread would corrupt it.
* **Instruments are addressed by numeric token**, not symbol, and futures
  tokens change every expiry. The instrument dump is fetched once per day and
  the nearest non-expired contract is resolved from it.

Access tokens expire daily around 06:00 IST, so `history` and `stream` fail
soft: the feed falls back to the synthetic provider and the UI reports that
the broker session needs re-authenticating rather than showing a blank chart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...config import DATA_DIR
from ...instruments import get_instrument, get_timeframe
from ... import runtime_settings
from .base import MarketDataProvider, ProviderTick, TickHandler

log = logging.getLogger("meridian.zerodha")

INSTRUMENT_CACHE = DATA_DIR / "kite_instruments.json"

#: Our timeframe → Kite interval. Kite has no weekly candle, so 1w is built by
#: resampling daily bars.
INTERVALS: Dict[str, str] = {
    "1m": "minute", "5m": "5minute", "15m": "15minute", "30m": "30minute",
    "1h": "60minute", "4h": "60minute", "1d": "day", "1w": "day",
}

#: Kite caps each historical request by interval. Staying inside these avoids
#: a 400 that would otherwise look like "no data".
MAX_DAYS: Dict[str, int] = {
    "minute": 60, "3minute": 90, "5minute": 90, "10minute": 90,
    "15minute": 180, "30minute": 180, "60minute": 365, "day": 2000,
}

#: Kite allows ~3 historical requests/second.
_RATE_LIMIT = asyncio.Semaphore(2)


class ZerodhaError(RuntimeError):
    pass


class ZerodhaProvider(MarketDataProvider):
    name = "zerodha"
    live = True

    def __init__(self) -> None:
        self._kite = None
        self._ticker = None
        self._instruments: Dict[str, Dict[str, Any]] = {}
        self._instruments_day: Optional[int] = None
        self._token_to_symbol: Dict[int, str] = {}
        self._last_error: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── session ───────────────────────────────────────────────

    def _client(self):
        """Build (or reuse) an authenticated KiteConnect client."""
        cfg = runtime_settings.get()
        if not cfg.zerodha_api_key or not cfg.zerodha_access_token:
            raise ZerodhaError("Zerodha API key or access token is not configured")

        if self._kite is not None:
            return self._kite
        try:
            from kiteconnect import KiteConnect
        except ImportError as exc:
            raise ZerodhaError(
                "kiteconnect is not installed — run: pip install kiteconnect") from exc

        kite = KiteConnect(api_key=cfg.zerodha_api_key)
        kite.set_access_token(cfg.zerodha_access_token)
        self._kite = kite
        return kite

    def reset(self) -> None:
        """Drop the cached client so new credentials take effect."""
        self._kite = None
        self._last_error = None

    def supports(self, symbol: str) -> bool:
        try:
            instrument = get_instrument(symbol)
        except KeyError:
            return False
        return bool(instrument.exchange) and runtime_settings.store.zerodha_ready

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    # ── instrument resolution ─────────────────────────────────

    def _load_instrument_dump(self) -> List[Dict[str, Any]]:
        """Kite's instrument list, cached to disk for the trading day."""
        today = int(time.time() // 86400)
        if self._instruments_day == today and self._instruments:
            return []

        if INSTRUMENT_CACHE.exists():
            try:
                payload = json.loads(INSTRUMENT_CACHE.read_text())
                if payload.get("day") == today:
                    self._instruments_day = today
                    return payload.get("instruments", [])
            except (ValueError, OSError):
                pass

        kite = self._client()
        rows: List[Dict[str, Any]] = []
        for exchange in ("NFO", "CDS", "MCX", "NSE"):
            try:
                rows.extend(kite.instruments(exchange))
            except Exception as exc:  # noqa: BLE001
                log.warning("instrument dump failed for %s: %s", exchange, exc)

        slim = [
            {
                "instrument_token": row["instrument_token"],
                "tradingsymbol": row["tradingsymbol"],
                "name": row.get("name") or "",
                "exchange": row["exchange"],
                "segment": row.get("segment") or "",
                "instrument_type": row.get("instrument_type") or "",
                "expiry": str(row.get("expiry") or ""),
                "lot_size": row.get("lot_size") or 0,
                "tick_size": row.get("tick_size") or 0,
            }
            for row in rows
        ]
        try:
            INSTRUMENT_CACHE.write_text(json.dumps({"day": today, "instruments": slim}))
        except OSError:
            pass
        self._instruments_day = today
        return slim

    def _resolve(self, symbol: str) -> Tuple[int, Dict[str, Any]]:
        """Find the tradable contract for one of our symbols.

        For futures this is the nearest expiry that has not passed — the front
        month is where the liquidity is, and a stale token returns empty
        candles rather than an error.
        """
        cached = self._instruments.get(symbol)
        today = int(time.time() // 86400)
        if cached and self._instruments_day == today:
            return cached["instrument_token"], cached

        instrument = get_instrument(symbol)
        rows = self._load_instrument_dump()
        if not rows and cached:
            return cached["instrument_token"], cached

        target = (instrument.broker_symbol or instrument.symbol).upper()
        exchange = instrument.exchange

        candidates: List[Dict[str, Any]] = []
        for row in rows:
            if exchange == "NSE" and instrument.asset_class == "index":
                # The index itself is an NSE "INDICES" segment instrument.
                if row["exchange"] == "NSE" and row["tradingsymbol"].upper() in (
                    "NIFTY 50" if target == "NIFTY" else "NIFTY BANK",
                ):
                    candidates.append(row)
                continue
            if row["exchange"] != (exchange if exchange != "NSE" else "NFO"):
                continue
            if not row["tradingsymbol"].upper().startswith(target):
                continue
            if row["instrument_type"] not in ("FUT", "EQ", ""):
                continue
            candidates.append(row)

        if not candidates:
            raise ZerodhaError(
                "No Kite contract found for {} on {}".format(target, exchange))

        def sort_key(row: Dict[str, Any]):
            expiry = row.get("expiry") or "9999-12-31"
            return expiry

        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        future = [r for r in candidates if (r.get("expiry") or "9999-12-31") >= today_iso]
        chosen = sorted(future or candidates, key=sort_key)[0]

        self._instruments[symbol] = chosen
        self._token_to_symbol[int(chosen["instrument_token"])] = symbol
        log.info("resolved %s → %s (%s, token %s, lot %s)", symbol,
                 chosen["tradingsymbol"], chosen["exchange"],
                 chosen["instrument_token"], chosen["lot_size"])
        return int(chosen["instrument_token"]), chosen

    async def contract_for(self, symbol: str) -> Optional[Dict[str, Any]]:
        """The resolved contract, for display in the UI."""
        try:
            _, row = await asyncio.get_running_loop().run_in_executor(
                None, self._resolve, symbol)
            return row
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            return None

    # ── history ───────────────────────────────────────────────

    def _fetch_history(self, symbol: str, timeframe: str, bars: int) -> List[List[float]]:
        token, _ = self._resolve(symbol)
        kite = self._client()
        interval = INTERVALS[timeframe]
        tf_seconds = get_timeframe(timeframe).seconds

        # Ask for enough calendar days to cover `bars`, allowing for the fact
        # that exchanges are shut most of the day.
        if interval == "day":
            span_days = int(bars * 1.55) + 10
        else:
            bars_per_day = max(1, int(6.5 * 3600 / min(tf_seconds, 3600)))
            span_days = int(bars / bars_per_day * 1.6) + 7

        span_days = min(span_days, MAX_DAYS.get(interval, 90))
        to_date = datetime.now()
        from_date = to_date - timedelta(days=span_days)

        candles = kite.historical_data(token, from_date, to_date, interval)
        rows: List[List[float]] = []
        for candle in candles:
            stamp = candle["date"]
            epoch = stamp.timestamp() if hasattr(stamp, "timestamp") else float(stamp)
            rows.append([
                float(epoch), float(candle["open"]), float(candle["high"]),
                float(candle["low"]), float(candle["close"]),
                float(candle.get("volume") or 0.0),
            ])

        if timeframe in ("4h", "1w"):
            rows = _resample(rows, tf_seconds)
        return rows[-bars:]

    async def history(self, symbol: str, timeframe: str, bars: int) -> List[List[float]]:
        if timeframe not in INTERVALS:
            return []
        try:
            async with _RATE_LIMIT:
                loop = asyncio.get_running_loop()
                rows = await loop.run_in_executor(
                    None, self._fetch_history, symbol, timeframe, bars)
                await asyncio.sleep(0.34)   # stay under ~3 req/s
            self._last_error = None
            return rows
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            log.warning("kite history failed for %s %s: %s", symbol, timeframe, exc)
            return []

    # ── live ticks ────────────────────────────────────────────

    async def stream(self, symbols: Sequence[str], handler: TickHandler) -> None:
        cfg = runtime_settings.get()
        tradable = [s for s in symbols if self.supports(s)]
        if not tradable:
            return

        try:
            from kiteconnect import KiteTicker
        except ImportError:
            log.warning("kiteconnect not installed — no Zerodha live ticks")
            return

        loop = asyncio.get_running_loop()
        self._loop = loop

        tokens: List[int] = []
        for symbol in tradable:
            try:
                token, _ = await loop.run_in_executor(None, self._resolve, symbol)
                tokens.append(token)
            except Exception as exc:  # noqa: BLE001
                log.warning("cannot subscribe %s: %s", symbol, exc)

        if not tokens:
            return

        ticker = KiteTicker(cfg.zerodha_api_key, cfg.zerodha_access_token)
        self._ticker = ticker

        def on_ticks(_ws, ticks):
            # Runs on the Twisted thread — hop back to the event loop.
            for tick in ticks:
                symbol = self._token_to_symbol.get(int(tick.get("instrument_token", 0)))
                price = tick.get("last_price")
                if not symbol or not price:
                    continue
                try:
                    inst = get_instrument(symbol)
                except KeyError:
                    continue
                half = inst.pip_size / 2
                stamp = tick.get("exchange_timestamp")
                event = ProviderTick(
                    symbol=symbol,
                    time=stamp.timestamp() if hasattr(stamp, "timestamp") else time.time(),
                    price=float(price),
                    bid=round(float(price) - half, inst.digits),
                    ask=round(float(price) + half, inst.digits),
                    volume=float(tick.get("last_traded_quantity") or 0.0),
                )
                asyncio.run_coroutine_threadsafe(handler(event), loop)

        def on_connect(ws, _response):
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_QUOTE, tokens)
            log.info("kite ticker connected for %d instruments", len(tokens))

        def on_error(_ws, code, reason):
            self._last_error = "ticker error {}: {}".format(code, reason)
            log.warning("kite ticker error %s: %s", code, reason)

        ticker.on_ticks = on_ticks
        ticker.on_connect = on_connect
        ticker.on_error = on_error

        # Twisted owns its own reactor thread; the coroutine just parks here
        # until cancelled so the feed's task lifecycle still works.
        await loop.run_in_executor(None, lambda: ticker.connect(threaded=True))
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise
        finally:
            try:
                ticker.close()
            except Exception:  # noqa: BLE001
                pass

    async def close(self) -> None:
        if self._ticker is not None:
            try:
                self._ticker.close()
            except Exception:  # noqa: BLE001
                pass
            self._ticker = None


def _resample(rows: List[List[float]], tf_seconds: int) -> List[List[float]]:
    """Aggregate finer candles into 4H / weekly buckets."""
    from ..store import bar_open_time

    buckets: Dict[int, List[float]] = {}
    for row in rows:
        key = bar_open_time(row[0], tf_seconds)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = [float(key), row[1], row[2], row[3], row[4], row[5]]
        else:
            bucket[2] = max(bucket[2], row[2])
            bucket[3] = min(bucket[3], row[3])
            bucket[4] = row[4]
            bucket[5] += row[5]
    return [buckets[key] for key in sorted(buckets)]


# ── login helpers used by the settings API ────────────────────

def login_url(api_key: str) -> str:
    return "https://kite.zerodha.com/connect/login?v=3&api_key={}".format(api_key)


def exchange_request_token(api_key: str, api_secret: str, request_token: str) -> Dict[str, Any]:
    """Trade a one-time request_token for a daily access_token."""
    try:
        from kiteconnect import KiteConnect
    except ImportError as exc:
        raise ZerodhaError(
            "kiteconnect is not installed — run: pip install kiteconnect") from exc

    kite = KiteConnect(api_key=api_key)
    data = kite.generate_session(request_token, api_secret=api_secret)
    return {
        "access_token": data["access_token"],
        "user_id": data.get("user_id"),
        "user_name": data.get("user_name"),
        "login_time": str(data.get("login_time") or ""),
    }
