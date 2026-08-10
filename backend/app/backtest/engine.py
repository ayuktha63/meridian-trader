"""Historical replay.

Runs the same confluence logic the live engine uses, bar by bar, with strict
look-ahead discipline:

* Indicators are vectorised once, then read only at index `i`.
* Swings are detected once over the whole series but a swing is only visible
  from `swing.index + right` — the bar on which it was actually confirmed.
* Entries fill at the close of the signal bar. Exits are checked on subsequent
  bars only.
* When a bar's range covers both stop and target, the **stop** is assumed to
  fill first. Without tick data you cannot know the order, and assuming the
  favourable one is how backtests come to lie.

Claude is not called during replay — thousands of API round-trips would be
slow and expensive, and the AI component is only 10% of the score anyway. The
`use_ai` flag exists so a future sampled-AI mode has a home.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ..data.feed import feed
from ..data.store import store
from ..instruments import get_instrument
from ..schemas.journal import (
    BacktestRequest, BacktestResult, BacktestTrade,
)
from ..analysis import indicators as ind
from ..analysis.structure import detect_structure_events, find_swings
from ..analysis.candlesticks import PATTERNS, build_ctx
from . import metrics as metrics_mod

log = logging.getLogger("meridian.backtest")

SWING_LEFT = SWING_RIGHT = 3
WARMUP = 210
MAX_HOLD_BARS = 200

# Live weights minus the AI component, renormalised to 1.0.
WEIGHTS = {"trend": 0.278, "momentum": 0.222, "volume": 0.167,
           "structure": 0.222, "pattern": 0.111}


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _series(df: pd.DataFrame) -> Dict[str, np.ndarray]:
    close = df["close"]
    macd = ind.macd(close)
    adx = ind.adx(df, 14)
    st = ind.supertrend(df)
    bb = ind.bollinger(close)
    obv = ind.obv(df)
    volume = df["volume"]
    atr = ind.atr(df, 14)
    range_high = df["high"].rolling(20, min_periods=10).max()
    range_low = df["low"].rolling(20, min_periods=10).min()

    return {
        "ema20": ind.ema(close, 20).to_numpy(),
        "ema50": ind.ema(close, 50).to_numpy(),
        "ema200": ind.ema(close, 200).to_numpy(),
        "atr": atr.to_numpy(),
        # Compression proxy for the breakout strategy: today's ATR against its
        # own recent average. Below ~0.8x means volatility has contracted —
        # the same idea as `snap.bb_squeeze` in the live engine, cheaper to
        # compute vectorised than a true Bollinger-bandwidth percentile.
        "atr_ratio": (atr / atr.rolling(50, min_periods=20).mean()).to_numpy(),
        "rsi": ind.rsi(close, 14).to_numpy(),
        "macd_hist": macd["hist"].to_numpy(),
        "macd_line": macd["line"].to_numpy(),
        "adx": adx["adx"].to_numpy(),
        "plus_di": adx["plus_di"].to_numpy(),
        "minus_di": adx["minus_di"].to_numpy(),
        "st_dir": st["direction"].to_numpy(),
        "bb_pctb": bb["pctb"].to_numpy(),
        "bb_mid": bb["mid"].to_numpy(),
        "obv_slope": obv.diff(20).to_numpy(),
        "mfi": ind.mfi(df, 14).to_numpy(),
        "rvol": (volume / volume.rolling(20, min_periods=5).mean()).to_numpy(),
        "range_high": range_high.to_numpy(),
        "range_low": range_low.to_numpy(),
    }


def _candle_bias(ctx, i: int) -> float:
    """Net directional weight of candlestick patterns completing at bar i."""
    score = 0.0
    for _, (fn, direction, bars, reliability) in PATTERNS.items():
        if direction == "neutral" or i - bars + 1 < 1:
            continue
        try:
            if fn(ctx, i):
                score += reliability if direction == "bullish" else -reliability
        except (IndexError, ZeroDivisionError):
            continue
    return _clamp(score / 1.5)


def _structure_timeline(df: pd.DataFrame, atr_mean: float):
    """Per-bar structural bias, respecting swing confirmation lag."""
    n = len(df)
    swings = find_swings(df, SWING_LEFT, SWING_RIGHT)
    events, _ = detect_structure_events(df, swings, SWING_RIGHT, atr_mean)

    bias = np.zeros(n)
    labels: List[float] = []
    cursor = 0
    ordered = sorted(swings, key=lambda s: s.index + SWING_RIGHT)
    recent: List[str] = []

    for i in range(n):
        while cursor < len(ordered) and ordered[cursor].index + SWING_RIGHT <= i:
            if ordered[cursor].label:
                recent.append(ordered[cursor].label)
                recent[:] = recent[-6:]
            cursor += 1
        if recent:
            bull = sum(1 for l in recent if l in ("HH", "HL"))
            bear = sum(1 for l in recent if l in ("LH", "LL"))
            bias[i] = (bull - bear) / len(recent)
        labels.append(bias[i])

    event_bias = np.zeros(n)
    for event in events:
        sign = 1.0 if event.direction == "bullish" else -1.0
        weight = {"BOS": 0.8, "CHoCH": 0.7, "liquidity_sweep": 0.5}.get(event.kind, 0.3)
        # Decay the influence of an event over the following 20 bars.
        for offset in range(0, 20):
            j = event.index + offset
            if j >= n:
                break
            event_bias[j] += sign * weight * (1.0 - offset / 20.0)

    return np.clip(bias, -1, 1), np.clip(event_bias, -1, 1)


def _mean_reversion_setup(i: int, s: Dict[str, np.ndarray], close: np.ndarray,
                          struct_bias: np.ndarray, event_bias: np.ndarray):
    """Vectorised approximation of `analysis/strategies.mean_reversion`: fade
    a stretched move back toward the mean, only while the market is ranging.
    Not a byte-for-byte port (that version reads a per-bar structure
    snapshot this fast loop doesn't build) but the same three gates: ranging,
    stretched, no fresh break against the fade."""
    adx, rsi, pctb = s["adx"][i], s["rsi"][i], s["bb_pctb"][i]
    if np.isnan(adx) or np.isnan(rsi):
        return None
    ranging = adx < 22 and abs(struct_bias[i]) < 0.3
    stretched = rsi <= 32 or rsi >= 68 or (not np.isnan(pctb) and (pctb <= 0.05 or pctb >= 0.95))
    if not (ranging and stretched):
        return None

    long = rsi < 50
    if abs(event_bias[i]) > 0.5 and (event_bias[i] > 0) != long:
        return None  # a fresh BOS against the fade means the range broke

    atr = s["atr"][i]
    entry = float(close[i])
    stop = entry - atr * 1.2 if long else entry + atr * 1.2
    mid = s["bb_mid"][i]
    target = (float(mid) if not np.isnan(mid) and abs(mid - entry) > atr * 0.8
             else (entry + atr * 1.6 if long else entry - atr * 1.6))
    return ("BUY" if long else "SELL", entry, stop, target)


def _breakout_setup(i: int, s: Dict[str, np.ndarray], close: np.ndarray):
    """Vectorised approximation of `analysis/strategies.breakout`: a decisive
    move out of compression, at the range edge, confirmed by volume."""
    atr_ratio, rvol = s["atr_ratio"][i], s["rvol"][i]
    high, low = s["range_high"][i], s["range_low"][i]
    if np.isnan(atr_ratio) or np.isnan(rvol) or np.isnan(high) or np.isnan(low):
        return None
    compressed = atr_ratio < 0.8
    if not compressed or rvol < 1.2:
        return None

    price = float(close[i])
    atr = s["atr"][i]
    dist_high, dist_low = abs(price - high), abs(price - low)
    if min(dist_high, dist_low) > atr * 1.2:
        return None  # not actually at the edge

    long = dist_high <= dist_low
    width = max(high - low, atr * 2)
    stop = (low + atr * 0.3) if long else (high - atr * 0.3)
    if abs(price - stop) > atr * 3:
        stop = price - atr * 1.5 if long else price + atr * 1.5
    target = price + width * 0.9 if long else price - width * 0.9
    return ("BUY" if long else "SELL", price, stop, target)


def _signal(i: int, s: Dict[str, np.ndarray], close: np.ndarray,
            struct_bias: np.ndarray, event_bias: np.ndarray,
            candle_bias: float) -> Dict[str, float]:
    # Trend
    trend_parts = []
    if not np.isnan(s["ema20"][i]) and not np.isnan(s["ema50"][i]):
        trend_parts.append(0.5 if close[i] > s["ema20"][i] else -0.5)
        trend_parts.append(0.5 if s["ema20"][i] > s["ema50"][i] else -0.5)
    if not np.isnan(s["ema200"][i]):
        trend_parts.append(0.5 if close[i] > s["ema200"][i] else -0.5)
    di_total = s["plus_di"][i] + s["minus_di"][i]
    if di_total > 0 and not np.isnan(s["adx"][i]):
        trend_parts.append(_clamp(((s["plus_di"][i] - s["minus_di"][i]) / di_total)
                                  * min(s["adx"][i] / 28.0, 1.0)))
    if s["st_dir"][i]:
        trend_parts.append(0.7 * float(s["st_dir"][i]))
    trend = _clamp(float(np.mean(trend_parts))) if trend_parts else 0.0

    # Momentum
    momentum_parts = []
    rsi = s["rsi"][i]
    if not np.isnan(rsi):
        momentum_parts.append(_clamp((rsi - 50.0) / 20.0))
        if rsi > 78:
            momentum_parts.append(-0.35)
        elif rsi < 22:
            momentum_parts.append(0.35)
    hist, line = s["macd_hist"][i], s["macd_line"][i]
    if not np.isnan(hist):
        scale = max(abs(line), abs(hist), 1e-9)
        momentum_parts.append(_clamp(hist / scale))
    momentum = _clamp(float(np.mean(momentum_parts))) if momentum_parts else 0.0

    # Volume
    volume_parts = []
    if not np.isnan(s["obv_slope"][i]):
        volume_parts.append(_clamp(s["obv_slope"][i] / (abs(s["obv_slope"][i]) + 1e-9)) * 0.8)
    if not np.isnan(s["mfi"][i]):
        volume_parts.append(_clamp((s["mfi"][i] - 50.0) / 25.0) * 0.8)
    volume = _clamp(float(np.mean(volume_parts))) if volume_parts else 0.0
    rvol = s["rvol"][i]
    if not np.isnan(rvol) and rvol < 0.6:
        volume *= 0.6

    structure = _clamp(struct_bias[i] * 0.6 + event_bias[i] * 0.4)

    components = {
        "trend": trend, "momentum": momentum, "volume": volume,
        "structure": structure, "pattern": candle_bias,
    }
    directional = sum(components[k] * WEIGHTS[k] for k in WEIGHTS)

    meaningful = [(k, v) for k, v in components.items() if abs(v) > 0.12]
    if meaningful and abs(directional) > 1e-9:
        dominant = 1 if directional > 0 else -1
        agreeing = sum(WEIGHTS[k] for k, v in meaningful
                       if (1 if v > 0 else -1) == dominant)
        agreement = agreeing / sum(WEIGHTS[k] for k, _ in meaningful)
    else:
        agreement = 0.0

    confidence = abs(directional) * 100.0 * (0.55 + 0.45 * agreement)
    return {"directional": directional, "agreement": agreement,
            "confidence": min(100.0, confidence), **components}


async def run_backtest(request: BacktestRequest) -> BacktestResult:
    symbol = request.symbol.upper()
    timeframe = request.timeframe.lower()
    instrument = get_instrument(symbol)

    await feed.ensure_history(symbol, timeframe, request.bars)
    df = store.frame(symbol, timeframe, request.bars)

    result = BacktestResult(
        symbol=symbol, timeframe=timeframe, strategy=request.strategy,
        bars_tested=0, from_time=0, to_time=0,
        metrics=metrics_mod.BacktestMetrics(ending_balance=request.starting_balance),
    )
    if df is None or len(df) < WARMUP + 50:
        result.notes.append(
            "Not enough history to backtest ({} bars, need {})".format(
                0 if df is None else len(df), WARMUP + 50))
        return result

    started = time.perf_counter()
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    times = df["time"].to_numpy()
    n = len(df)

    s = _series(df)
    atr_mean = float(np.nanmean(s["atr"])) or 1e-6
    struct_bias, event_bias = _structure_timeline(df, atr_mean)
    ctx = build_ctx(df)

    balance = request.starting_balance
    risk_amount = balance * (request.risk_pct / 100.0)
    equity_curve: List[Dict[str, float]] = [
        {"time": float(times[WARMUP]), "equity": balance}
    ]
    trades: List[BacktestTrade] = []
    open_trade: Optional[BacktestTrade] = None
    open_index = 0

    for i in range(WARMUP, n):
        # ── manage the open position first ───────────────────
        if open_trade is not None:
            bars_held = i - open_index
            hit_stop = (low[i] <= open_trade.stop_loss if open_trade.direction == "BUY"
                        else high[i] >= open_trade.stop_loss)
            hit_target = (high[i] >= open_trade.take_profit if open_trade.direction == "BUY"
                          else low[i] <= open_trade.take_profit)

            exit_price = None
            outcome = None
            if hit_stop:            # pessimistic ordering — stop wins ties
                exit_price, outcome = open_trade.stop_loss, "loss"
            elif hit_target:
                exit_price, outcome = open_trade.take_profit, "win"
            elif bars_held >= MAX_HOLD_BARS:
                exit_price, outcome = close[i], "breakeven"

            if exit_price is not None:
                risk = abs(open_trade.entry - open_trade.stop_loss)
                move = ((exit_price - open_trade.entry)
                        if open_trade.direction == "BUY"
                        else (open_trade.entry - exit_price))
                r_multiple = move / risk if risk > 0 else 0.0
                if outcome == "breakeven" and abs(r_multiple) > 0.15:
                    outcome = "win" if r_multiple > 0 else "loss"

                open_trade.exit_price = round(float(exit_price), instrument.digits)
                open_trade.exit_time = int(times[i])
                open_trade.outcome = outcome
                open_trade.r_multiple = round(float(r_multiple), 3)
                open_trade.pnl = round(float(r_multiple * risk_amount), 2)
                open_trade.bars_held = bars_held

                balance += open_trade.pnl
                risk_amount = balance * (request.risk_pct / 100.0)
                equity_curve.append({"time": float(times[i]), "equity": round(balance, 2)})
                open_trade = None
            else:
                continue

        if open_trade is not None:
            continue

        atr = s["atr"][i]
        if np.isnan(atr) or atr <= 0:
            continue

        signal = _signal(i, s, close, struct_bias, event_bias, _candle_bias(ctx, i))

        # Mean reversion and breakout have their own direction/entry/stop/
        # target rule — the confluence score below is not used to size or
        # aim these, only as a baseline "is anything obviously wrong" floor.
        if request.strategy == "mean_reversion":
            setup = _mean_reversion_setup(i, s, close, struct_bias, event_bias)
            if setup is None:
                continue
            direction, entry, stop, target = setup
        elif request.strategy == "breakout":
            setup = _breakout_setup(i, s, close)
            if setup is None:
                continue
            direction, entry, stop, target = setup
        else:
            if signal["confidence"] < request.min_confidence:
                continue
            if request.strategy == "structure" and abs(struct_bias[i]) < 0.3:
                continue
            if request.strategy == "trend_pullback":
                rsi = s["rsi"][i]
                aligned_long = signal["directional"] > 0 and rsi < 48
                aligned_short = signal["directional"] < 0 and rsi > 52
                if not (aligned_long or aligned_short):
                    continue

            direction = "BUY" if signal["directional"] > 0 else "SELL"
            entry = float(close[i])
            stop_distance = float(atr * request.atr_stop_mult)
            stop = entry - stop_distance if direction == "BUY" else entry + stop_distance
            target = (entry + stop_distance * request.risk_reward if direction == "BUY"
                      else entry - stop_distance * request.risk_reward)

        if request.strategy == "mean_reversion":
            reason = "ADX {:.0f} (ranging) · RSI {:.0f} · %B {:.2f} — fading back toward the mean".format(
                s["adx"][i], s["rsi"][i], s["bb_pctb"][i])
        elif request.strategy == "breakout":
            reason = "ATR ratio {:.2f} (compressed) · RVOL {:.1f}x — break from the range edge".format(
                s["atr_ratio"][i], s["rvol"][i])
        else:
            reason = ("trend {:+.2f} · momentum {:+.2f} · structure {:+.2f} · "
                      "volume {:+.2f} · pattern {:+.2f} ({:.0f}% agreement)".format(
                          signal["trend"], signal["momentum"], signal["structure"],
                          signal["volume"], signal["pattern"], signal["agreement"] * 100))

        open_trade = BacktestTrade(
            index=i, time=int(times[i]), direction=direction,
            entry=round(entry, instrument.digits),
            stop_loss=round(stop, instrument.digits),
            take_profit=round(target, instrument.digits),
            confidence=round(signal["confidence"], 1),
            reason=reason,
        )
        open_index = i
        trades.append(open_trade)

    if open_trade is not None:
        open_trade.outcome = "open"
        open_trade.bars_held = n - 1 - open_index

    result.bars_tested = n - WARMUP
    result.from_time = int(times[WARMUP])
    result.to_time = int(times[-1])
    result.trades = trades[-400:]
    result.equity_curve = equity_curve[-1000:]
    result.metrics = metrics_mod.compute(
        trades, request.starting_balance, equity_curve, result.bars_tested)

    result.notes.append(
        "Replayed {} bars in {:.0f}ms. Entries fill at signal-bar close; when a bar "
        "spans both stop and target the stop is assumed to fill first.".format(
            result.bars_tested, (time.perf_counter() - started) * 1000))
    result.notes.append(
        "Claude was not consulted during replay — this measures the deterministic "
        "confluence engine only, which is 90% of the live score.")
    if len(trades) < 25:
        result.notes.append(
            "Only {} trades — too small a sample to draw conclusions from. Widen the "
            "window or lower the confidence threshold.".format(len(trades)))
    if request.use_ai:
        result.notes.append(
            "use_ai was requested but per-bar AI replay is intentionally not "
            "implemented; it would cost thousands of API calls per run.")

    return result
