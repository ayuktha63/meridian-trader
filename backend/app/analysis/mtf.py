"""Multi-timeframe fusion.

Each timeframe is scored independently to a directional value in [-1, +1],
then combined with macro-weighted averaging. Alignment matters more than the
average: six timeframes at +0.4 is a far better setup than three at +1.0 and
three at -0.2, and the report says so explicitly.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from ..instruments import MTF_LADDER, MTF_WEIGHTS, get_timeframe
from ..schemas.analysis import Bias, MTFReport, TimeframeView
from . import indicators as ind
from .structure import find_swings

LABEL = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
         "1h": "1H", "4h": "4H", "1d": "Daily", "1w": "Weekly"}


def score_timeframe(df: pd.DataFrame, timeframe: str) -> TimeframeView:
    view = TimeframeView(timeframe=timeframe)
    if df is None or len(df) < 60:
        view.note = "insufficient history"
        return view

    close = df["close"]
    price = float(close.iloc[-1])
    view.close = price

    ema20 = ind.ema(close, 20).iloc[-1]
    ema50 = ind.ema(close, 50).iloc[-1]
    ema200 = ind.ema(close, 200).iloc[-1] if len(df) >= 200 else ema50

    # 1. EMA stack (structureless but fast trend proxy)
    stack = 0.0
    if price > ema20:
        stack += 0.34
    else:
        stack -= 0.34
    if ema20 > ema50:
        stack += 0.33
    else:
        stack -= 0.33
    if ema50 > ema200:
        stack += 0.33
    else:
        stack -= 0.33
    view.ema_stack = ("bullish" if stack > 0.3 else "bearish" if stack < -0.3 else "mixed")

    # 2. Directional movement
    adx_df = ind.adx(df, 14)
    adx_val = float(adx_df["adx"].iloc[-1]) if pd.notna(adx_df["adx"].iloc[-1]) else 0.0
    plus = float(adx_df["plus_di"].iloc[-1] or 0.0)
    minus = float(adx_df["minus_di"].iloc[-1] or 0.0)
    di_total = plus + minus
    di_score = ((plus - minus) / di_total) if di_total > 0 else 0.0
    di_score *= min(adx_val / 28.0, 1.0)
    view.adx = round(adx_val, 1)

    # 3. Momentum
    rsi_val = float(ind.rsi(close, 14).iloc[-1])
    view.rsi = round(rsi_val, 1)
    rsi_score = max(-1.0, min(1.0, (rsi_val - 50.0) / 22.0))

    # 4. Structure from confirmed swings
    swings = find_swings(df.tail(320), 3, 3)
    labels = [s.label for s in swings[-6:] if s.label]
    bull = sum(1 for l in labels if l in ("HH", "HL"))
    bear = sum(1 for l in labels if l in ("LH", "LL"))
    struct_score = ((bull - bear) / max(len(labels), 1)) if labels else 0.0
    if struct_score > 0.34:
        view.structure = "uptrend"
    elif struct_score < -0.34:
        view.structure = "downtrend"
    else:
        view.structure = "ranging"

    # 5. SuperTrend as a regime confirmation
    st = ind.supertrend(df.tail(300))
    st_dir = int(st["direction"].iloc[-1]) if len(st) else 0

    score = (stack * 0.30 + di_score * 0.25 + rsi_score * 0.15
             + struct_score * 0.20 + st_dir * 0.10)
    score = max(-1.0, min(1.0, score))
    view.score = round(score, 3)
    view.bias = (Bias.BULLISH if score > 0.18 else
                 Bias.BEARISH if score < -0.18 else Bias.NEUTRAL)

    # A pullback inside a trend is worth naming — it is where entries live.
    if view.structure == "uptrend" and rsi_val < 45:
        view.note = "pullback in uptrend"
    elif view.structure == "downtrend" and rsi_val > 55:
        view.note = "pullback in downtrend"
    elif adx_val < 18:
        view.note = "no directional conviction"
    elif view.bias != Bias.NEUTRAL:
        view.note = "{} with ADX {:.0f}".format(view.bias.value, adx_val)

    swing_highs = [s.price for s in swings if s.kind == "swing_high"]
    swing_lows = [s.price for s in swings if s.kind == "swing_low"]
    if view.bias == Bias.BULLISH and swing_lows:
        view.key_level = round(swing_lows[-1], 6)
    elif view.bias == Bias.BEARISH and swing_highs:
        view.key_level = round(swing_highs[-1], 6)

    return view


def build_report(frames: Dict[str, pd.DataFrame],
                 ladder: Optional[List[str]] = None) -> MTFReport:
    ladder = ladder or MTF_LADDER
    report = MTFReport()

    views: List[TimeframeView] = []
    for tf in ladder:
        df = frames.get(tf)
        if df is None or df.empty:
            continue
        views.append(score_timeframe(df, tf))
    report.views = views
    if not views:
        report.narrative = "No timeframe data available"
        return report

    total_weight = sum(MTF_WEIGHTS.get(v.timeframe, 0.1) for v in views) or 1.0
    weighted = sum(v.score * MTF_WEIGHTS.get(v.timeframe, 0.1) for v in views) / total_weight
    report.weighted_score = round(weighted, 3)
    report.aligned_bias = (Bias.BULLISH if weighted > 0.15 else
                           Bias.BEARISH if weighted < -0.15 else Bias.NEUTRAL)

    dominant = 1 if weighted > 0 else -1
    agreeing = sum(MTF_WEIGHTS.get(v.timeframe, 0.1)
                   for v in views if (v.score > 0) == (dominant > 0) and abs(v.score) > 0.12)
    considered = sum(MTF_WEIGHTS.get(v.timeframe, 0.1)
                     for v in views if abs(v.score) > 0.12) or 1.0
    report.alignment_score = round(agreeing / considered, 3)

    # Conflicts are reported macro-first: a daily/4H disagreement outranks 1m noise.
    ordered = sorted(views, key=lambda v: -get_timeframe(v.timeframe).rank)
    for i, higher in enumerate(ordered):
        for lower in ordered[i + 1:]:
            if higher.bias == Bias.NEUTRAL or lower.bias == Bias.NEUTRAL:
                continue
            if higher.bias != lower.bias:
                report.conflicts.append(
                    "{} {} vs {} {}".format(LABEL.get(higher.timeframe, higher.timeframe),
                                            higher.bias.value,
                                            LABEL.get(lower.timeframe, lower.timeframe),
                                            lower.bias.value)
                )
    report.conflicts = report.conflicts[:4]

    parts = []
    for view in ordered:
        label = LABEL.get(view.timeframe, view.timeframe)
        descriptor = view.note if view.note and "pullback" in view.note else view.bias.value
        parts.append("{} {}".format(label, descriptor))
    report.narrative = " · ".join(parts)
    return report


def summarise(report: MTFReport) -> Dict[str, object]:
    return {
        "aligned_bias": report.aligned_bias.value,
        "weighted_score": report.weighted_score,
        "alignment_score": report.alignment_score,
        "timeframes": [
            {
                "tf": LABEL.get(v.timeframe, v.timeframe),
                "bias": v.bias.value,
                "structure": v.structure,
                "score": v.score,
                "rsi": v.rsi,
                "adx": v.adx,
                "note": v.note,
            }
            for v in report.views
        ],
        "conflicts": report.conflicts,
        "narrative": report.narrative,
    }
