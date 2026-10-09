"""Chart (geometric) pattern detection.

Patterns are derived from confirmed swing points rather than raw bars, then
validated by least-squares fit quality and touch count. Anything that cannot
be justified by at least three touches with an R² above threshold is not
reported — a pattern you have to squint at is not a pattern.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..schemas.analysis import PatternHit, SwingPoint

MIN_R2 = 0.62


def _fit(xs: List[float], ys: List[float]) -> Optional[Tuple[float, float, float]]:
    """Least-squares line → (slope, intercept, r²)."""
    if len(xs) < 2:
        return None
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    if np.ptp(x) == 0:
        return None
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-18 else 1.0
    return float(slope), float(intercept), float(max(0.0, min(1.0, r2)))


def _line_points(slope: float, intercept: float, i0: int, i1: int,
                 times: np.ndarray) -> List[Dict[str, float]]:
    return [
        {"time": float(times[i0]), "price": float(slope * i0 + intercept)},
        {"time": float(times[i1]), "price": float(slope * i1 + intercept)},
    ]


def _flat(slope: float, span: int, atr: float) -> bool:
    return abs(slope * span) < 0.55 * atr


# ── converging / parallel structures ──────────────────────────

def detect_trendline_structures(df: pd.DataFrame, swings: List[SwingPoint],
                                atr: float) -> List[PatternHit]:
    highs = [s for s in swings if s.kind == "swing_high"][-5:]
    lows = [s for s in swings if s.kind == "swing_low"][-5:]
    if len(highs) < 3 or len(lows) < 3:
        return []

    times = df["time"].to_numpy()
    n = len(df)
    upper = _fit([s.index for s in highs], [s.price for s in highs])
    lower = _fit([s.index for s in lows], [s.price for s in lows])
    if not upper or not lower:
        return []

    us, ui, ur2 = upper
    ls, li, lr2 = lower
    if ur2 < MIN_R2 or lr2 < MIN_R2:
        return []

    i0 = min(highs[0].index, lows[0].index)
    i1 = max(highs[-1].index, lows[-1].index)
    span = max(i1 - i0, 1)
    width_start = (us * i0 + ui) - (ls * i0 + li)
    width_end = (us * i1 + ui) - (ls * i1 + li)
    if width_start <= 0 or width_end <= 0:
        return []

    converging = width_end < 0.68 * width_start
    diverging = width_end > 1.45 * width_start
    upper_flat = _flat(us, span, atr)
    lower_flat = _flat(ls, span, atr)
    fit_quality = (ur2 + lr2) / 2.0
    touches = len(highs) + len(lows)
    base_conf = round(min(0.92, 0.35 + fit_quality * 0.4 + touches * 0.03), 3)

    apex_price = float(us * i1 + ui)
    height = width_start

    name = direction = None
    target = invalidation = None

    if converging and upper_flat and ls > 0:
        name, direction = "Ascending Triangle", "bullish"
        target = apex_price + height
        invalidation = float(ls * i1 + li)
    elif converging and lower_flat and us < 0:
        name, direction = "Descending Triangle", "bearish"
        target = float(ls * i1 + li) - height
        invalidation = apex_price
    elif converging and us < 0 < ls:
        name, direction = "Symmetrical Triangle", "neutral"
        target = None
        invalidation = None
    elif converging and us > 0 and ls > 0:
        name, direction = "Rising Wedge", "bearish"
        target = float(ls * i1 + li) - height
        invalidation = apex_price
    elif converging and us < 0 and ls < 0:
        name, direction = "Falling Wedge", "bullish"
        target = apex_price + height
        invalidation = float(ls * i1 + li)
    elif diverging:
        name, direction = "Broadening Formation", "neutral"
    elif upper_flat and lower_flat:
        name, direction = "Rectangle", "neutral"
        target = None
    elif us > 0 and ls > 0:
        name, direction = "Ascending Channel", "bullish"
        invalidation = float(ls * i1 + li)
    elif us < 0 and ls < 0:
        name, direction = "Descending Channel", "bearish"
        invalidation = apex_price

    if name is None:
        return []

    return [PatternHit(
        name=name, category="chart", direction=direction,
        start_index=i0, end_index=min(i1, n - 1),
        start_time=int(times[i0]), end_time=int(times[min(i1, n - 1)]),
        confidence=base_conf, target=target, invalidation=invalidation,
        points=(_line_points(us, ui, i0, min(i1 + 5, n - 1), times)
                + _line_points(ls, li, i0, min(i1 + 5, n - 1), times)),
        note="{} touches, fit R²={:.2f}".format(touches, fit_quality),
    )]


# ── reversal formations ───────────────────────────────────────

def detect_head_and_shoulders(df: pd.DataFrame, swings: List[SwingPoint],
                              atr: float) -> List[PatternHit]:
    times = df["time"].to_numpy()  # noqa: F841
    hits: List[PatternHit] = []
    seq = swings[-9:]
    if len(seq) < 5:
        return hits

    for start in range(len(seq) - 4):
        window = seq[start:start + 5]
        kinds = [s.kind for s in window]

        # High-Low-High-Low-High → head & shoulders top
        if kinds == ["swing_high", "swing_low", "swing_high", "swing_low", "swing_high"]:
            ls, t1, head, t2, rs = window
            if not (head.price > ls.price and head.price > rs.price):
                continue
            if abs(ls.price - rs.price) > 0.9 * atr:
                continue
            if abs(t1.price - t2.price) > 1.2 * atr:
                continue
            neckline = (t1.price + t2.price) / 2.0
            height = head.price - neckline
            if height < 1.2 * atr:
                continue
            hits.append(PatternHit(
                name="Head and Shoulders", category="chart", direction="bearish",
                start_index=ls.index, end_index=rs.index,
                start_time=ls.time, end_time=rs.time,
                confidence=round(min(0.9, 0.55 + (height / (atr * 6))), 3),
                target=neckline - height, invalidation=head.price,
                points=[{"time": float(s.time), "price": s.price} for s in window],
                note="Neckline {:.5f}, measured move {:.1f} ATR".format(
                    neckline, height / (atr + 1e-9)),
            ))

        # Low-High-Low-High-Low → inverse head & shoulders
        if kinds == ["swing_low", "swing_high", "swing_low", "swing_high", "swing_low"]:
            ls, t1, head, t2, rs = window
            if not (head.price < ls.price and head.price < rs.price):
                continue
            if abs(ls.price - rs.price) > 0.9 * atr:
                continue
            if abs(t1.price - t2.price) > 1.2 * atr:
                continue
            neckline = (t1.price + t2.price) / 2.0
            height = neckline - head.price
            if height < 1.2 * atr:
                continue
            hits.append(PatternHit(
                name="Inverse Head and Shoulders", category="chart", direction="bullish",
                start_index=ls.index, end_index=rs.index,
                start_time=ls.time, end_time=rs.time,
                confidence=round(min(0.9, 0.55 + (height / (atr * 6))), 3),
                target=neckline + height, invalidation=head.price,
                points=[{"time": float(s.time), "price": s.price} for s in window],
                note="Neckline {:.5f}, measured move {:.1f} ATR".format(
                    neckline, height / (atr + 1e-9)),
            ))

    return hits[-2:]


def detect_double_patterns(df: pd.DataFrame, swings: List[SwingPoint],
                           atr: float) -> List[PatternHit]:
    hits: List[PatternHit] = []
    highs = [s for s in swings if s.kind == "swing_high"][-4:]
    lows = [s for s in swings if s.kind == "swing_low"][-4:]

    def check(points: List[SwingPoint], counter: List[SwingPoint], top: bool) -> None:
        if len(points) < 2:
            return
        a, b = points[-2], points[-1]
        if b.index - a.index < 5:
            return
        if abs(a.price - b.price) > 0.55 * atr:
            return
        between = [s for s in counter if a.index < s.index < b.index]
        if not between:
            return
        pivot = (min(between, key=lambda s: s.price) if top
                 else max(between, key=lambda s: s.price))
        height = abs(a.price - pivot.price)
        if height < 1.1 * atr:
            return
        triple = len(points) >= 3 and abs(points[-3].price - a.price) <= 0.55 * atr
        name = ("Triple Top" if triple else "Double Top") if top else (
            "Triple Bottom" if triple else "Double Bottom")
        hits.append(PatternHit(
            name=name, category="chart",
            direction="bearish" if top else "bullish",
            start_index=a.index, end_index=b.index,
            start_time=a.time, end_time=b.time,
            confidence=round(min(0.88, 0.5 + height / (atr * 6) + (0.08 if triple else 0)), 3),
            target=(pivot.price - height) if top else (pivot.price + height),
            invalidation=max(a.price, b.price) if top else min(a.price, b.price),
            points=[{"time": float(s.time), "price": s.price} for s in (a, pivot, b)],
            note="Neckline {:.5f}".format(pivot.price),
        ))

    check(highs, lows, True)
    check(lows, highs, False)
    return hits


# ── continuation formations ───────────────────────────────────

def detect_flags(df: pd.DataFrame, atr: float) -> List[PatternHit]:
    n = len(df)
    if n < 30:
        return []
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    times = df["time"].to_numpy()
    hits: List[PatternHit] = []

    for consol in range(5, 16):
        end = n - 1
        cons_start = end - consol
        pole_start = max(cons_start - 12, 0)
        if cons_start - pole_start < 4:
            continue

        pole = close[cons_start] - close[pole_start]
        if abs(pole) < 3.0 * atr:
            continue

        seg_high = high[cons_start:end + 1]
        seg_low = low[cons_start:end + 1]
        cons_range = float(seg_high.max() - seg_low.min())
        if cons_range > 0.45 * abs(pole):
            continue

        idx = list(range(cons_start, end + 1))
        up_fit = _fit(idx, list(seg_high))
        lo_fit = _fit(idx, list(seg_low))
        if not up_fit or not lo_fit:
            continue

        bullish = pole > 0
        counter_slope = (up_fit[0] + lo_fit[0]) / 2.0
        # A flag drifts against the pole; a pennant converges.
        converging = (up_fit[0] < 0 < lo_fit[0]) or (up_fit[0] - lo_fit[0]) < -1e-12
        drifts_against = (counter_slope < 0) if bullish else (counter_slope > 0)
        if not (converging or drifts_against):
            continue

        name = "Pennant" if converging else ("Bull Flag" if bullish else "Bear Flag")
        target = close[end] + pole * 0.85
        hits.append(PatternHit(
            name=name, category="chart",
            direction="bullish" if bullish else "bearish",
            start_index=pole_start, end_index=end,
            start_time=int(times[pole_start]), end_time=int(times[end]),
            confidence=round(min(0.85, 0.45 + abs(pole) / (atr * 12)), 3),
            target=float(target),
            invalidation=float(seg_low.min() if bullish else seg_high.max()),
            points=[
                {"time": float(times[pole_start]), "price": float(close[pole_start])},
                {"time": float(times[cons_start]), "price": float(close[cons_start])},
                {"time": float(times[end]), "price": float(close[end])},
            ],
            note="Pole {:.1f} ATR, consolidation {} bars".format(abs(pole) / (atr + 1e-9), consol),
        ))
        break

    return hits


def detect_cup_and_handle(df: pd.DataFrame, atr: float) -> List[PatternHit]:
    n = len(df)
    if n < 60:
        return []
    close = df["close"].to_numpy()
    times = df["time"].to_numpy()

    for window in (80, 60, 45):
        if n < window + 10:
            continue
        seg = close[n - window:n - 8]
        if len(seg) < 20:
            continue
        xs = np.arange(len(seg), dtype=float)
        # A cup is a positive-curvature quadratic with its vertex near the middle.
        coeffs = np.polyfit(xs, seg, 2)
        if coeffs[0] <= 0:
            continue
        vertex = -coeffs[1] / (2 * coeffs[0])
        if not (0.3 * len(seg) < vertex < 0.7 * len(seg)):
            continue
        pred = np.polyval(coeffs, xs)
        ss_res = float(((seg - pred) ** 2).sum())
        ss_tot = float(((seg - seg.mean()) ** 2).sum())
        if ss_tot <= 0 or 1 - ss_res / ss_tot < 0.68:
            continue

        left_rim, right_rim = float(seg[0]), float(seg[-1])
        depth = right_rim - float(seg.min())
        if depth < 2.0 * atr or abs(left_rim - right_rim) > 1.2 * atr:
            continue

        handle = close[n - 8:]
        if handle.max() > right_rim + 0.6 * atr:
            continue
        if right_rim - handle.min() > 0.6 * depth:
            continue

        return [PatternHit(
            name="Cup and Handle", category="chart", direction="bullish",
            start_index=n - window, end_index=n - 1,
            start_time=int(times[n - window]), end_time=int(times[n - 1]),
            confidence=round(min(0.82, 0.5 + depth / (atr * 10)), 3),
            target=right_rim + depth, invalidation=float(handle.min()),
            points=[
                {"time": float(times[n - window]), "price": left_rim},
                {"time": float(times[n - window + int(vertex)]), "price": float(seg.min())},
                {"time": float(times[n - 9]), "price": right_rim},
            ],
            note="Cup depth {:.1f} ATR with handle".format(depth / (atr + 1e-9)),
        )]
    return []


# ── orchestration ─────────────────────────────────────────────

def detect(df: pd.DataFrame, swings: List[SwingPoint], atr: float,
           max_hits: int = 6) -> List[PatternHit]:
    if df is None or len(df) < 30:
        return []
    atr = max(atr, 1e-9)
    hits: List[PatternHit] = []
    for fn, args in (
        (detect_trendline_structures, (df, swings, atr)),
        (detect_head_and_shoulders, (df, swings, atr)),
        (detect_double_patterns, (df, swings, atr)),
        (detect_flags, (df, atr)),
        (detect_cup_and_handle, (df, atr)),
    ):
        try:
            hits.extend(fn(*args))
        except (ValueError, IndexError, np.linalg.LinAlgError):
            continue

    hits.sort(key=lambda h: (-h.confidence, -h.end_index))
    return hits[:max_hits]
