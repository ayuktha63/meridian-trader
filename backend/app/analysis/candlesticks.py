"""Candlestick pattern recognition — 60 patterns, native NumPy.

Each pattern is a predicate over a shared `Ctx` of precomputed bar metrics
(body, shadows, gaps, relative size, prior trend). Patterns are scanned over
a trailing window and only the ones that completed recently are reported,
because a Morning Star from 300 bars ago is history, not a signal.

Reliability weights are the conventional Nison/Bulkowski ordering: three-bar
reversals with confirmation outrank single indecision candles.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

import numpy as np
import pandas as pd

from ..schemas.analysis import PatternHit


@dataclass
class Ctx:
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    t: np.ndarray
    body: np.ndarray
    rng: np.ndarray
    upper: np.ndarray
    lower: np.ndarray
    avg_body: np.ndarray
    avg_range: np.ndarray

    def bull(self, i: int) -> bool:
        return self.c[i] > self.o[i]

    def bear(self, i: int) -> bool:
        return self.c[i] < self.o[i]

    def doji(self, i: int, thresh: float = 0.1) -> bool:
        return self.body[i] <= thresh * max(self.rng[i], 1e-12)

    def long_body(self, i: int, mult: float = 1.3) -> bool:
        return self.body[i] >= mult * self.avg_body[i]

    def short_body(self, i: int, mult: float = 0.55) -> bool:
        return self.body[i] <= mult * self.avg_body[i]

    def gap_up(self, i: int) -> bool:
        return self.l[i] > self.h[i - 1]

    def gap_down(self, i: int) -> bool:
        return self.h[i] < self.l[i - 1]

    def body_gap_up(self, i: int) -> bool:
        return min(self.o[i], self.c[i]) > max(self.o[i - 1], self.c[i - 1])

    def body_gap_down(self, i: int) -> bool:
        return max(self.o[i], self.c[i]) < min(self.o[i - 1], self.c[i - 1])

    def downtrend(self, i: int, n: int = 5) -> bool:
        j = max(i - n, 0)
        return self.c[i - 1] < self.c[j] if i > 0 else False

    def uptrend(self, i: int, n: int = 5) -> bool:
        j = max(i - n, 0)
        return self.c[i - 1] > self.c[j] if i > 0 else False

    def near(self, a: float, b: float, i: int, tol: float = 0.12) -> bool:
        return abs(a - b) <= tol * max(self.avg_range[i], 1e-12)


def build_ctx(df: pd.DataFrame) -> Ctx:
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    t = df["time"].to_numpy(dtype=float)
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-12)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    avg_body = pd.Series(body).rolling(14, min_periods=1).mean().to_numpy()
    avg_range = pd.Series(rng).rolling(14, min_periods=1).mean().to_numpy()
    return Ctx(o, h, l, c, t, body, rng, upper, lower,
               np.maximum(avg_body, 1e-12), np.maximum(avg_range, 1e-12))


Predicate = Callable[[Ctx, int], bool]

# name -> (predicate, direction, bars_used, reliability 0-1)
PATTERNS: Dict[str, Tuple[Predicate, str, int, float]] = {}


def pattern(name: str, direction: str, bars: int, reliability: float):
    def wrap(fn: Predicate) -> Predicate:
        PATTERNS[name] = (fn, direction, bars, reliability)
        return fn
    return wrap


# ── single candle ─────────────────────────────────────────────

@pattern("Doji", "neutral", 1, 0.30)
def _doji(x: Ctx, i: int) -> bool:
    return x.doji(i) and x.rng[i] > 0.4 * x.avg_range[i]


@pattern("Dragonfly Doji", "bullish", 1, 0.55)
def _dragonfly(x: Ctx, i: int) -> bool:
    return x.doji(i) and x.lower[i] > 2 * x.body[i] and x.upper[i] < 0.15 * x.rng[i]


@pattern("Gravestone Doji", "bearish", 1, 0.55)
def _gravestone(x: Ctx, i: int) -> bool:
    return x.doji(i) and x.upper[i] > 2 * x.body[i] and x.lower[i] < 0.15 * x.rng[i]


@pattern("Long-Legged Doji", "neutral", 1, 0.35)
def _long_legged(x: Ctx, i: int) -> bool:
    return (x.doji(i) and x.upper[i] > 0.3 * x.rng[i] and x.lower[i] > 0.3 * x.rng[i])


@pattern("Four Price Doji", "neutral", 1, 0.20)
def _four_price(x: Ctx, i: int) -> bool:
    return x.rng[i] <= 0.08 * x.avg_range[i]


@pattern("Hammer", "bullish", 1, 0.60)
def _hammer(x: Ctx, i: int) -> bool:
    return (x.downtrend(i) and x.lower[i] >= 2 * x.body[i]
            and x.upper[i] <= 0.35 * x.body[i] and x.body[i] > 0.05 * x.rng[i])


@pattern("Inverted Hammer", "bullish", 1, 0.52)
def _inv_hammer(x: Ctx, i: int) -> bool:
    return (x.downtrend(i) and x.upper[i] >= 2 * x.body[i]
            and x.lower[i] <= 0.35 * x.body[i] and x.body[i] > 0.05 * x.rng[i])


@pattern("Hanging Man", "bearish", 1, 0.55)
def _hanging_man(x: Ctx, i: int) -> bool:
    return (x.uptrend(i) and x.lower[i] >= 2 * x.body[i]
            and x.upper[i] <= 0.35 * x.body[i] and x.body[i] > 0.05 * x.rng[i])


@pattern("Shooting Star", "bearish", 1, 0.62)
def _shooting_star(x: Ctx, i: int) -> bool:
    return (x.uptrend(i) and x.upper[i] >= 2 * x.body[i]
            and x.lower[i] <= 0.35 * x.body[i] and x.body[i] > 0.05 * x.rng[i])


@pattern("Takuri Line", "bullish", 1, 0.50)
def _takuri(x: Ctx, i: int) -> bool:
    return x.downtrend(i) and x.lower[i] >= 3 * x.body[i] and x.upper[i] < 0.1 * x.rng[i]


@pattern("Bullish Marubozu", "bullish", 1, 0.58)
def _maru_bull(x: Ctx, i: int) -> bool:
    return (x.bull(i) and x.long_body(i)
            and x.upper[i] < 0.05 * x.rng[i] and x.lower[i] < 0.05 * x.rng[i])


@pattern("Bearish Marubozu", "bearish", 1, 0.58)
def _maru_bear(x: Ctx, i: int) -> bool:
    return (x.bear(i) and x.long_body(i)
            and x.upper[i] < 0.05 * x.rng[i] and x.lower[i] < 0.05 * x.rng[i])


@pattern("Spinning Top", "neutral", 1, 0.28)
def _spinning_top(x: Ctx, i: int) -> bool:
    return (x.body[i] < 0.35 * x.rng[i] and x.upper[i] > 0.25 * x.rng[i]
            and x.lower[i] > 0.25 * x.rng[i] and not x.doji(i))


@pattern("High Wave", "neutral", 1, 0.32)
def _high_wave(x: Ctx, i: int) -> bool:
    return (x.rng[i] > 1.8 * x.avg_range[i] and x.body[i] < 0.25 * x.rng[i])


@pattern("Bullish Belt Hold", "bullish", 1, 0.48)
def _belt_bull(x: Ctx, i: int) -> bool:
    return (x.downtrend(i) and x.bull(i) and x.long_body(i)
            and x.lower[i] < 0.04 * x.rng[i])


@pattern("Bearish Belt Hold", "bearish", 1, 0.48)
def _belt_bear(x: Ctx, i: int) -> bool:
    return (x.uptrend(i) and x.bear(i) and x.long_body(i)
            and x.upper[i] < 0.04 * x.rng[i])


@pattern("Long Line Bullish", "bullish", 1, 0.40)
def _long_line_bull(x: Ctx, i: int) -> bool:
    return x.bull(i) and x.body[i] > 1.8 * x.avg_body[i]


@pattern("Long Line Bearish", "bearish", 1, 0.40)
def _long_line_bear(x: Ctx, i: int) -> bool:
    return x.bear(i) and x.body[i] > 1.8 * x.avg_body[i]


@pattern("Short Line", "neutral", 1, 0.22)
def _short_line(x: Ctx, i: int) -> bool:
    return x.body[i] < 0.3 * x.avg_body[i] and not x.doji(i)


# ── two candles ───────────────────────────────────────────────

@pattern("Bullish Engulfing", "bullish", 2, 0.72)
def _engulf_bull(x: Ctx, i: int) -> bool:
    return (x.bear(i - 1) and x.bull(i)
            and x.c[i] >= x.o[i - 1] and x.o[i] <= x.c[i - 1]
            and x.body[i] > x.body[i - 1] and x.downtrend(i))


@pattern("Bearish Engulfing", "bearish", 2, 0.72)
def _engulf_bear(x: Ctx, i: int) -> bool:
    return (x.bull(i - 1) and x.bear(i)
            and x.c[i] <= x.o[i - 1] and x.o[i] >= x.c[i - 1]
            and x.body[i] > x.body[i - 1] and x.uptrend(i))


@pattern("Bullish Harami", "bullish", 2, 0.55)
def _harami_bull(x: Ctx, i: int) -> bool:
    return (x.bear(i - 1) and x.bull(i) and x.long_body(i - 1)
            and x.o[i] > x.c[i - 1] and x.c[i] < x.o[i - 1] and x.short_body(i))


@pattern("Bearish Harami", "bearish", 2, 0.55)
def _harami_bear(x: Ctx, i: int) -> bool:
    return (x.bull(i - 1) and x.bear(i) and x.long_body(i - 1)
            and x.o[i] < x.c[i - 1] and x.c[i] > x.o[i - 1] and x.short_body(i))


@pattern("Bullish Harami Cross", "bullish", 2, 0.62)
def _harami_cross_bull(x: Ctx, i: int) -> bool:
    return (x.bear(i - 1) and x.long_body(i - 1) and x.doji(i)
            and max(x.o[i], x.c[i]) < x.o[i - 1] and min(x.o[i], x.c[i]) > x.c[i - 1])


@pattern("Bearish Harami Cross", "bearish", 2, 0.62)
def _harami_cross_bear(x: Ctx, i: int) -> bool:
    return (x.bull(i - 1) and x.long_body(i - 1) and x.doji(i)
            and max(x.o[i], x.c[i]) < x.c[i - 1] and min(x.o[i], x.c[i]) > x.o[i - 1])


@pattern("Piercing Line", "bullish", 2, 0.68)
def _piercing(x: Ctx, i: int) -> bool:
    mid = (x.o[i - 1] + x.c[i - 1]) / 2
    return (x.bear(i - 1) and x.bull(i) and x.long_body(i - 1)
            and x.o[i] < x.l[i - 1] and mid < x.c[i] < x.o[i - 1])


@pattern("Dark Cloud Cover", "bearish", 2, 0.68)
def _dark_cloud(x: Ctx, i: int) -> bool:
    mid = (x.o[i - 1] + x.c[i - 1]) / 2
    return (x.bull(i - 1) and x.bear(i) and x.long_body(i - 1)
            and x.o[i] > x.h[i - 1] and x.o[i - 1] < x.c[i] < mid)


@pattern("Tweezer Bottom", "bullish", 2, 0.52)
def _tweezer_bottom(x: Ctx, i: int) -> bool:
    return (x.downtrend(i) and x.near(x.l[i], x.l[i - 1], i, 0.08)
            and x.bear(i - 1) and x.bull(i))


@pattern("Tweezer Top", "bearish", 2, 0.52)
def _tweezer_top(x: Ctx, i: int) -> bool:
    return (x.uptrend(i) and x.near(x.h[i], x.h[i - 1], i, 0.08)
            and x.bull(i - 1) and x.bear(i))


@pattern("Bullish Kicker", "bullish", 2, 0.78)
def _kicker_bull(x: Ctx, i: int) -> bool:
    return (x.bear(i - 1) and x.bull(i) and x.o[i] >= x.o[i - 1]
            and x.gap_up(i) and x.long_body(i))


@pattern("Bearish Kicker", "bearish", 2, 0.78)
def _kicker_bear(x: Ctx, i: int) -> bool:
    return (x.bull(i - 1) and x.bear(i) and x.o[i] <= x.o[i - 1]
            and x.gap_down(i) and x.long_body(i))


@pattern("Bullish Separating Lines", "bullish", 2, 0.50)
def _separating_bull(x: Ctx, i: int) -> bool:
    return (x.bear(i - 1) and x.bull(i) and x.near(x.o[i], x.o[i - 1], i, 0.06)
            and x.long_body(i))


@pattern("Bearish Separating Lines", "bearish", 2, 0.50)
def _separating_bear(x: Ctx, i: int) -> bool:
    return (x.bull(i - 1) and x.bear(i) and x.near(x.o[i], x.o[i - 1], i, 0.06)
            and x.long_body(i))


@pattern("Matching Low", "bullish", 2, 0.45)
def _matching_low(x: Ctx, i: int) -> bool:
    return (x.downtrend(i) and x.bear(i - 1) and x.bear(i)
            and x.near(x.c[i], x.c[i - 1], i, 0.05))


@pattern("Matching High", "bearish", 2, 0.45)
def _matching_high(x: Ctx, i: int) -> bool:
    return (x.uptrend(i) and x.bull(i - 1) and x.bull(i)
            and x.near(x.c[i], x.c[i - 1], i, 0.05))


@pattern("On Neck Line", "bearish", 2, 0.42)
def _on_neck(x: Ctx, i: int) -> bool:
    return (x.downtrend(i) and x.bear(i - 1) and x.bull(i)
            and x.o[i] < x.l[i - 1] and x.near(x.c[i], x.l[i - 1], i, 0.06))


@pattern("In Neck Line", "bearish", 2, 0.42)
def _in_neck(x: Ctx, i: int) -> bool:
    return (x.downtrend(i) and x.bear(i - 1) and x.bull(i)
            and x.o[i] < x.l[i - 1] and x.near(x.c[i], x.c[i - 1], i, 0.08)
            and x.c[i] >= x.c[i - 1])


@pattern("Thrusting Line", "bearish", 2, 0.40)
def _thrusting(x: Ctx, i: int) -> bool:
    mid = (x.o[i - 1] + x.c[i - 1]) / 2
    return (x.downtrend(i) and x.bear(i - 1) and x.bull(i)
            and x.o[i] < x.l[i - 1] and x.c[i - 1] < x.c[i] < mid)


@pattern("Bullish Counterattack", "bullish", 2, 0.48)
def _counter_bull(x: Ctx, i: int) -> bool:
    return (x.downtrend(i) and x.bear(i - 1) and x.bull(i)
            and x.o[i] < x.l[i - 1] and x.near(x.c[i], x.c[i - 1], i, 0.07))


@pattern("Bearish Counterattack", "bearish", 2, 0.48)
def _counter_bear(x: Ctx, i: int) -> bool:
    return (x.uptrend(i) and x.bull(i - 1) and x.bear(i)
            and x.o[i] > x.h[i - 1] and x.near(x.c[i], x.c[i - 1], i, 0.07))


@pattern("Bullish Doji Star", "bullish", 2, 0.55)
def _doji_star_bull(x: Ctx, i: int) -> bool:
    return (x.downtrend(i) and x.bear(i - 1) and x.long_body(i - 1)
            and x.doji(i) and x.body_gap_down(i))


@pattern("Bearish Doji Star", "bearish", 2, 0.55)
def _doji_star_bear(x: Ctx, i: int) -> bool:
    return (x.uptrend(i) and x.bull(i - 1) and x.long_body(i - 1)
            and x.doji(i) and x.body_gap_up(i))


# ── three candles ─────────────────────────────────────────────

@pattern("Morning Star", "bullish", 3, 0.80)
def _morning_star(x: Ctx, i: int) -> bool:
    mid = (x.o[i - 2] + x.c[i - 2]) / 2
    return (x.bear(i - 2) and x.long_body(i - 2) and x.short_body(i - 1)
            and x.bull(i) and x.c[i] > mid and x.downtrend(i - 1))


@pattern("Evening Star", "bearish", 3, 0.80)
def _evening_star(x: Ctx, i: int) -> bool:
    mid = (x.o[i - 2] + x.c[i - 2]) / 2
    return (x.bull(i - 2) and x.long_body(i - 2) and x.short_body(i - 1)
            and x.bear(i) and x.c[i] < mid and x.uptrend(i - 1))


@pattern("Morning Doji Star", "bullish", 3, 0.84)
def _morning_doji_star(x: Ctx, i: int) -> bool:
    mid = (x.o[i - 2] + x.c[i - 2]) / 2
    return (x.bear(i - 2) and x.long_body(i - 2) and x.doji(i - 1)
            and x.bull(i) and x.c[i] > mid)


@pattern("Evening Doji Star", "bearish", 3, 0.84)
def _evening_doji_star(x: Ctx, i: int) -> bool:
    mid = (x.o[i - 2] + x.c[i - 2]) / 2
    return (x.bull(i - 2) and x.long_body(i - 2) and x.doji(i - 1)
            and x.bear(i) and x.c[i] < mid)


@pattern("Three White Soldiers", "bullish", 3, 0.82)
def _three_soldiers(x: Ctx, i: int) -> bool:
    return (all(x.bull(j) for j in (i - 2, i - 1, i))
            and x.c[i] > x.c[i - 1] > x.c[i - 2]
            and all(x.body[j] > 0.7 * x.avg_body[j] for j in (i - 2, i - 1, i))
            and x.o[i - 1] > x.o[i - 2] and x.o[i] > x.o[i - 1]
            and all(x.upper[j] < 0.35 * x.body[j] for j in (i - 1, i)))


@pattern("Three Black Crows", "bearish", 3, 0.82)
def _three_crows(x: Ctx, i: int) -> bool:
    return (all(x.bear(j) for j in (i - 2, i - 1, i))
            and x.c[i] < x.c[i - 1] < x.c[i - 2]
            and all(x.body[j] > 0.7 * x.avg_body[j] for j in (i - 2, i - 1, i))
            and x.o[i - 1] < x.o[i - 2] and x.o[i] < x.o[i - 1]
            and all(x.lower[j] < 0.35 * x.body[j] for j in (i - 1, i)))


@pattern("Identical Three Crows", "bearish", 3, 0.76)
def _identical_crows(x: Ctx, i: int) -> bool:
    return (all(x.bear(j) for j in (i - 2, i - 1, i))
            and x.near(x.o[i - 1], x.c[i - 2], i, 0.06)
            and x.near(x.o[i], x.c[i - 1], i, 0.06))


@pattern("Three Inside Up", "bullish", 3, 0.74)
def _three_inside_up(x: Ctx, i: int) -> bool:
    return _harami_bull(x, i - 1) and x.bull(i) and x.c[i] > x.h[i - 1]


@pattern("Three Inside Down", "bearish", 3, 0.74)
def _three_inside_down(x: Ctx, i: int) -> bool:
    return _harami_bear(x, i - 1) and x.bear(i) and x.c[i] < x.l[i - 1]


@pattern("Three Outside Up", "bullish", 3, 0.76)
def _three_outside_up(x: Ctx, i: int) -> bool:
    return _engulf_bull(x, i - 1) and x.bull(i) and x.c[i] > x.c[i - 1]


@pattern("Three Outside Down", "bearish", 3, 0.76)
def _three_outside_down(x: Ctx, i: int) -> bool:
    return _engulf_bear(x, i - 1) and x.bear(i) and x.c[i] < x.c[i - 1]


@pattern("Bullish Abandoned Baby", "bullish", 3, 0.86)
def _abandoned_bull(x: Ctx, i: int) -> bool:
    return (x.bear(i - 2) and x.doji(i - 1) and x.bull(i)
            and x.gap_down(i - 1) and x.gap_up(i))


@pattern("Bearish Abandoned Baby", "bearish", 3, 0.86)
def _abandoned_bear(x: Ctx, i: int) -> bool:
    return (x.bull(i - 2) and x.doji(i - 1) and x.bear(i)
            and x.gap_up(i - 1) and x.gap_down(i))


@pattern("Bullish Tri-Star", "bullish", 3, 0.70)
def _tristar_bull(x: Ctx, i: int) -> bool:
    return (all(x.doji(j) for j in (i - 2, i - 1, i))
            and x.l[i - 1] < x.l[i - 2] and x.l[i - 1] < x.l[i] and x.downtrend(i - 2))


@pattern("Bearish Tri-Star", "bearish", 3, 0.70)
def _tristar_bear(x: Ctx, i: int) -> bool:
    return (all(x.doji(j) for j in (i - 2, i - 1, i))
            and x.h[i - 1] > x.h[i - 2] and x.h[i - 1] > x.h[i] and x.uptrend(i - 2))


@pattern("Stick Sandwich", "bullish", 3, 0.58)
def _stick_sandwich(x: Ctx, i: int) -> bool:
    return (x.bear(i - 2) and x.bull(i - 1) and x.bear(i)
            and x.near(x.c[i], x.c[i - 2], i, 0.06))


@pattern("Upside Gap Two Crows", "bearish", 3, 0.64)
def _upside_gap_two_crows(x: Ctx, i: int) -> bool:
    return (x.bull(i - 2) and x.long_body(i - 2) and x.bear(i - 1) and x.bear(i)
            and x.body_gap_up(i - 1) and x.o[i] > x.o[i - 1]
            and x.c[i] < x.c[i - 1] and x.c[i] > x.c[i - 2])


@pattern("Advance Block", "bearish", 3, 0.56)
def _advance_block(x: Ctx, i: int) -> bool:
    return (all(x.bull(j) for j in (i - 2, i - 1, i))
            and x.body[i] < x.body[i - 1] < x.body[i - 2]
            and x.upper[i] > x.body[i] * 0.6 and x.uptrend(i - 2))


@pattern("Deliberation", "bearish", 3, 0.54)
def _deliberation(x: Ctx, i: int) -> bool:
    return (all(x.bull(j) for j in (i - 2, i - 1, i))
            and x.long_body(i - 2) and x.long_body(i - 1)
            and x.short_body(i) and x.o[i] >= x.c[i - 1])


@pattern("Unique Three River Bottom", "bullish", 3, 0.62)
def _three_river(x: Ctx, i: int) -> bool:
    return (x.bear(i - 2) and x.long_body(i - 2)
            and x.bear(i - 1) and x.l[i - 1] < x.l[i - 2]
            and x.c[i - 1] > x.c[i - 2] and x.bull(i) and x.short_body(i)
            and x.c[i] < x.c[i - 1])


# ── four / five candles ───────────────────────────────────────

@pattern("Bullish Three Line Strike", "bullish", 4, 0.66)
def _three_line_strike_bull(x: Ctx, i: int) -> bool:
    return (all(x.bull(j) for j in (i - 3, i - 2, i - 1))
            and x.c[i - 1] > x.c[i - 2] > x.c[i - 3]
            and x.bear(i) and x.o[i] > x.c[i - 1] and x.c[i] < x.o[i - 3])


@pattern("Bearish Three Line Strike", "bearish", 4, 0.66)
def _three_line_strike_bear(x: Ctx, i: int) -> bool:
    return (all(x.bear(j) for j in (i - 3, i - 2, i - 1))
            and x.c[i - 1] < x.c[i - 2] < x.c[i - 3]
            and x.bull(i) and x.o[i] < x.c[i - 1] and x.c[i] > x.o[i - 3])


@pattern("Concealing Baby Swallow", "bullish", 4, 0.68)
def _concealing_baby(x: Ctx, i: int) -> bool:
    return (all(x.bear(j) for j in (i - 3, i - 2, i - 1, i))
            and _maru_bear(x, i - 3) and _maru_bear(x, i - 2)
            and x.h[i - 1] > x.c[i - 2] and x.h[i] > x.h[i - 1] and x.l[i] < x.l[i - 1])


@pattern("Ladder Bottom", "bullish", 5, 0.60)
def _ladder_bottom(x: Ctx, i: int) -> bool:
    return (all(x.bear(j) for j in (i - 4, i - 3, i - 2))
            and x.c[i - 2] < x.c[i - 3] < x.c[i - 4]
            and x.bear(i - 1) and x.upper[i - 1] > x.body[i - 1]
            and x.bull(i) and x.o[i] > x.h[i - 1])


@pattern("Rising Three Methods", "bullish", 5, 0.74)
def _rising_three(x: Ctx, i: int) -> bool:
    return (x.bull(i - 4) and x.long_body(i - 4)
            and all(x.short_body(j) for j in (i - 3, i - 2, i - 1))
            and all(x.l[j] > x.l[i - 4] and x.h[j] < x.h[i - 4] for j in (i - 3, i - 2, i - 1))
            and x.bull(i) and x.c[i] > x.c[i - 4])


@pattern("Falling Three Methods", "bearish", 5, 0.74)
def _falling_three(x: Ctx, i: int) -> bool:
    return (x.bear(i - 4) and x.long_body(i - 4)
            and all(x.short_body(j) for j in (i - 3, i - 2, i - 1))
            and all(x.l[j] > x.l[i - 4] and x.h[j] < x.h[i - 4] for j in (i - 3, i - 2, i - 1))
            and x.bear(i) and x.c[i] < x.c[i - 4])


@pattern("Mat Hold", "bullish", 5, 0.72)
def _mat_hold(x: Ctx, i: int) -> bool:
    return (x.bull(i - 4) and x.long_body(i - 4) and x.body_gap_up(i - 3)
            and all(x.short_body(j) for j in (i - 3, i - 2, i - 1))
            and x.bull(i) and x.c[i] > x.h[i - 4])


@pattern("Bullish Breakaway", "bullish", 5, 0.64)
def _breakaway_bull(x: Ctx, i: int) -> bool:
    return (x.bear(i - 4) and x.long_body(i - 4) and x.body_gap_down(i - 3)
            and x.c[i - 2] < x.c[i - 3] and x.c[i - 1] < x.c[i - 2]
            and x.bull(i) and x.c[i] > x.o[i - 3] and x.c[i] < x.c[i - 4])


@pattern("Bearish Breakaway", "bearish", 5, 0.64)
def _breakaway_bear(x: Ctx, i: int) -> bool:
    return (x.bull(i - 4) and x.long_body(i - 4) and x.body_gap_up(i - 3)
            and x.c[i - 2] > x.c[i - 3] and x.c[i - 1] > x.c[i - 2]
            and x.bear(i) and x.c[i] < x.o[i - 3] and x.c[i] > x.c[i - 4])


# ── scanner ───────────────────────────────────────────────────

def detect(df: pd.DataFrame, lookback: int = 12, max_hits: int = 12) -> List[PatternHit]:
    """Patterns completing within the last `lookback` bars, newest first."""
    n = len(df)
    if n < 16:
        return []
    x = build_ctx(df)
    start = max(n - lookback, 6)
    hits: List[PatternHit] = []

    for i in range(start, n):
        for name, (fn, direction, bars, reliability) in PATTERNS.items():
            if i - bars + 1 < 1:
                continue
            try:
                matched = fn(x, i)
            except (IndexError, ZeroDivisionError):
                continue
            if not matched:
                continue
            bars_ago = n - 1 - i
            # Recency decay — a pattern 10 bars back is far weaker than the last bar.
            recency = max(0.35, 1.0 - bars_ago * 0.06)
            hits.append(PatternHit(
                name=name, category="candlestick", direction=direction,
                start_index=i - bars + 1, end_index=i,
                start_time=int(x.t[i - bars + 1]), end_time=int(x.t[i]),
                confidence=round(reliability * recency, 3),
                note="{} bar{} ago".format(bars_ago, "" if bars_ago == 1 else "s"),
            ))

    hits.sort(key=lambda h: (-h.end_index, -h.confidence))
    # Drop pure-noise single candles when something stronger printed on the same bar.
    filtered: List[PatternHit] = []
    strong_bars = {h.end_index for h in hits if h.confidence >= 0.5}
    for hit in hits:
        if hit.confidence < 0.35 and hit.end_index in strong_bars:
            continue
        filtered.append(hit)
    return filtered[:max_hits]


PATTERN_COUNT = len(PATTERNS)
