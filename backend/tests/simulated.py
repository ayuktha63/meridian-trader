"""A generated market, for the test suite only.

The application has one source of prices, Angel One, and nothing behind it.
A test cannot have a broker session, so the suite installs this in its place
(see conftest.py): deterministic bars with the structure the analysis engine
exists to read — persistent trends, ranging regimes, volatility clustering
and volume that follows the session clock.

It answers to the name "synthetic", which is the name the evidence checks
refuse. Bars made here must never count as a measurement of a market, in a
test as anywhere else.

Each timeframe is generated from a deterministic per-symbol seed and then
rescaled so all timeframes terminate on the same price.
"""
from __future__ import annotations

import asyncio
import math
import time
import zlib
from datetime import datetime, timezone
from typing import Dict, List, Sequence, Tuple

import numpy as np

from app.data.providers.base import MarketDataProvider, ProviderTick, TickHandler
from app.data.sessions import INDIA_HOURS, is_market_open
from app.data.store import bar_open_time
from app.instruments import get_instrument, get_timeframe

TICK_HZ = 2.0

#: Activity by hour for the one made-up market here that is not an exchange
#: product: name -> (open hour UTC, close hour UTC, weight). The application
#: itself has no such clock any more; its markets are Indian exchanges.
SESSIONS: Dict[str, Tuple[int, int, float]] = {
    "sydney": (21, 6, 0.55), "tokyo": (0, 9, 0.75),
    "london": (7, 16, 1.25), "newyork": (12, 21, 1.20),
}


def _in_window(hour: int, start: int, end: int) -> bool:
    return start <= hour < end if start < end else (hour >= start or hour < end)

#: symbol -> (price to start from, daily sigma in percent). Made up, and only
#: ever read here.
SEEDS: Dict[str, Tuple[float, float]] = {
    "EURUSD": (1.0850, 0.45),
    "USDINR": (83.20, 0.30), "EURINR": (90.10, 0.45),
    "GBPINR": (105.60, 0.55), "JPYINR": (55.20, 0.50),
    "NIFTY": (24_500.0, 0.85), "BANKNIFTY": (52_000.0, 1.10),
    "MCXGOLD": (71_500.0, 0.95), "MCXSILVER": (88_000.0, 1.60),
    "MCXCRUDE": (6_400.0, 2.10),
    "RELIANCE": (1_320.0, 1.5), "HDFCBANK": (1_680.0, 1.5), "INFY": (1_880.0, 1.5),
    "TATAMOTORS": (760.0, 1.5), "SBIN": (820.0, 1.5), "TCS": (4_100.0, 1.2),
    "ITC": (420.0, 1.5),
}
DEFAULT_SEED = (100.0, 1.5)


def seed_of(symbol: str) -> Tuple[float, float]:
    return SEEDS.get(symbol.upper(), DEFAULT_SEED)


def liquidity_factor(ts: float, asset_class: str = "fx") -> float:
    """Relative activity multiplier at `ts`; 1.0 is an average hour."""
    if not is_market_open(ts, asset_class):
        return 0.05

    if asset_class in INDIA_HOURS:
        # Indian volume clusters at the open and again into the close.
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        start, end = INDIA_HOURS[asset_class]
        clock = dt.hour + dt.minute / 60.0
        progress = (clock - start) / max(end - start, 1e-6)
        return 0.7 + 0.6 * (1.0 - abs(progress - 0.5) * 2) ** 0.5 + (
            0.4 if progress < 0.12 or progress > 0.88 else 0.0)

    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    factor = 0.35
    for _, (start, end, weight) in SESSIONS.items():
        if _in_window(hour, start, end):
            factor += weight * 0.5
    if 12 <= hour < 16:      # London / New York overlap
        factor *= 1.25
    return max(0.15, min(2.2, factor))


def _seed(*parts) -> int:
    return zlib.crc32("|".join(str(p) for p in parts).encode()) & 0xFFFF_FFFF


class SimulatedProvider(MarketDataProvider):
    name = "synthetic"
    live = False

    def __init__(self) -> None:
        self._price: Dict[str, float] = {}
        self._vol_state: Dict[str, float] = {}
        self._drift_state: Dict[str, float] = {}
        self._rng = np.random.default_rng(_seed("meridian-live", int(time.time() // 3600)))

    # ── helpers ───────────────────────────────────────────────

    def supports(self, symbol: str) -> bool:
        try:
            get_instrument(symbol)
        except KeyError:
            return False
        return True

    def anchor_price(self, symbol: str) -> float:
        """Today's opening reference — stable within a UTC day, drifting across days."""
        if symbol in self._price:
            return self._price[symbol]
        inst = get_instrument(symbol)
        day = int(time.time() // 86400)
        rng = np.random.default_rng(_seed(symbol, day))
        # Random walk across days from the instrument's reference price.
        base_price, daily_vol_pct = seed_of(symbol)
        drift = float(np.sum(rng.normal(0, daily_vol_pct / 100.0, size=12)))
        price = base_price * math.exp(drift)
        self._price[symbol] = round(price, inst.digits)
        return self._price[symbol]

    def _bar_times(self, timeframe: str, bars: int, asset_class: str) -> List[int]:
        tf = get_timeframe(timeframe).seconds
        now = time.time()
        cursor = bar_open_time(now, tf)
        out: List[int] = []
        # Cap the backwards walk so a fully-closed weekend can't spin.
        guard = bars * 6 + 512
        while len(out) < bars and guard > 0:
            guard -= 1
            if is_market_open(cursor, asset_class) or tf >= 604_800:
                out.append(int(cursor))
            cursor -= tf
        return sorted(out)

    # ── history ───────────────────────────────────────────────

    async def history(self, symbol: str, timeframe: str, bars: int) -> List[List[float]]:
        return self.generate(symbol, timeframe, bars)

    def generate(self, symbol: str, timeframe: str, bars: int) -> List[List[float]]:
        inst = get_instrument(symbol)
        tf = get_timeframe(timeframe)
        times = self._bar_times(timeframe, bars, inst.asset_class)
        n = len(times)
        if n == 0:
            return []

        rng = np.random.default_rng(_seed(symbol, timeframe, int(time.time() // 86400)))

        # Per-bar sigma scaled from the instrument's daily vol.
        bars_per_day = max(1.0, 86400.0 / tf.seconds)
        base_sigma = (seed_of(symbol)[1] / 100.0) / math.sqrt(bars_per_day)

        # Regime process: slow OU drift + GARCH-style vol clustering.
        drift = 0.0
        vol = base_sigma
        theta = 0.035                      # drift mean-reversion
        drift_shock = base_sigma * 0.55
        alpha, beta = 0.09, 0.88           # GARCH(1,1)
        omega = base_sigma ** 2 * (1 - alpha - beta)

        log_price = np.zeros(n)
        sigmas = np.zeros(n)
        shocks = rng.normal(0.0, 1.0, size=n)
        # Fat tails: occasional news shocks.
        jump_mask = rng.random(n) < 0.012
        jump_size = rng.normal(0.0, base_sigma * 4.0, size=n) * jump_mask  # noqa: F841

        level = 0.0
        for i in range(n):
            drift = drift * (1 - theta) + rng.normal(0.0, drift_shock * math.sqrt(2 * theta))
            eps = shocks[i] * vol
            vol = math.sqrt(max(omega + alpha * eps * eps + beta * vol * vol, 1e-12))
            sigmas[i] = vol
            # Session liquidity modulates realised range on intraday frames.
            liq = liquidity_factor(times[i], inst.asset_class) if tf.seconds < 86400 else 1.0
            level += drift * 0.6 + eps * (0.6 + 0.4 * liq)
            log_price[i] = level

        closes = np.exp(log_price)
        # Rescale the path so every timeframe ends on the same live price.
        closes = closes / closes[-1] * self.anchor_price(symbol)

        opens = np.empty(n)
        opens[0] = closes[0] * (1 - float(rng.normal(0, base_sigma)))
        opens[1:] = closes[:-1]

        body_hi = np.maximum(opens, closes)
        body_lo = np.minimum(opens, closes)
        wick_scale = sigmas * closes * 0.85
        upper = np.abs(rng.normal(0.0, 1.0, n)) * wick_scale
        lower = np.abs(rng.normal(0.0, 1.0, n)) * wick_scale
        highs = body_hi + upper
        lows = np.maximum(body_lo - lower, 1e-9)

        # Volume: session activity x realised move, with a stochastic floor.
        rel_move = np.abs(closes - opens) / np.maximum(sigmas * closes, 1e-12)
        liq_vec = np.array([
            liquidity_factor(t, inst.asset_class) if tf.seconds < 86400 else 1.0
            for t in times
        ])
        base_vol = 1200.0 * math.sqrt(tf.seconds / 60.0)
        volumes = base_vol * liq_vec * (0.55 + 0.75 * rel_move) * rng.lognormal(0, 0.22, n)

        digits = inst.digits
        rows: List[List[float]] = []
        for i in range(n):
            o, h, l, c = (round(float(opens[i]), digits), round(float(highs[i]), digits),
                          round(float(lows[i]), digits), round(float(closes[i]), digits))
            h = max(h, o, c)
            l = min(l, o, c)
            rows.append([float(times[i]), o, h, l, c, round(float(volumes[i]), 2)])

        self._vol_state[symbol] = float(sigmas[-1])
        self._drift_state[symbol] = float(drift)
        return rows

    # ── live stream ───────────────────────────────────────────

    async def stream(self, symbols: Sequence[str], handler: TickHandler) -> None:
        hz = TICK_HZ
        interval = 1.0 / hz
        for sym in symbols:
            self.anchor_price(sym)

        while True:
            now = time.time()
            for sym in symbols:
                tick = self._next_tick(sym, now, interval)
                if tick is not None:
                    await handler(tick)
            await asyncio.sleep(interval)

    def _next_tick(self, symbol: str, now: float, dt_seconds: float) -> ProviderTick:
        inst = get_instrument(symbol)
        price = self._price.get(symbol) or self.anchor_price(symbol)
        liq = liquidity_factor(now, inst.asset_class)

        # Per-second sigma from the instrument's daily vol, scaled by session.
        base_price, daily_vol_pct = seed_of(symbol)
        sec_sigma = (daily_vol_pct / 100.0) / math.sqrt(86400.0) * (0.5 + liq)
        step = float(self._rng.normal(0.0, sec_sigma * math.sqrt(dt_seconds)))
        drift = self._drift_state.get(symbol, 0.0) * 0.00002
        price = price * math.exp(step + drift)

        # Keep the simulated market tethered to a plausible band.
        anchor = base_price
        if price > anchor * 1.30:
            price = anchor * 1.30
        elif price < anchor * 0.70:
            price = anchor * 0.70

        price = round(price, inst.digits)
        self._price[symbol] = price

        spread = inst.from_pips(inst.typical_spread_pips * (1.6 - min(liq, 1.4) * 0.6))
        volume = max(1.0, float(self._rng.lognormal(2.2, 0.55)) * liq)
        return ProviderTick(
            symbol=symbol,
            time=now,
            price=price,
            bid=round(price - spread / 2, inst.digits),
            ask=round(price + spread / 2, inst.digits),
            volume=volume,
        )
