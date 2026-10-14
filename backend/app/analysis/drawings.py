"""User-drawing intelligence.

The user draws a line; the backend has to know everything about it — where
it projects to now, how many times price actually touched it, whether those
touches produced a reaction, whether it has been broken cleanly or only
wicked through, and how it relates to current price. That evidence, not the
drawing itself, is what Claude reasons over.

Angles are reported ATR-normalised (degrees of arctan(slope / ATR)) so the
number means the same thing on NIFTY and USDINR and does not change when
the user zooms.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..instruments import Instrument
from ..schemas.drawings import (
    Drawing, DrawingAnalytics, DrawingType, LEVEL_TYPES, Point, TouchEvent, ZONE_TYPES,
)

FIB_RETRACEMENT = [0.0, 0.236, 0.382, 0.5, 0.618, 0.705, 0.786, 1.0]
FIB_EXTENSION = [0.0, 1.0, 1.272, 1.414, 1.618, 2.0, 2.618, 3.618]

TOUCH_TOLERANCE_ATR = 0.18
BREAK_TOLERANCE_ATR = 0.10
REACTION_WINDOW = 6


def _index_for_time(times: np.ndarray, ts: int) -> int:
    if len(times) == 0:
        return 0
    idx = int(np.searchsorted(times, ts, side="left"))
    return max(0, min(idx, len(times) - 1))


def _sorted_anchors(drawing: Drawing, times: np.ndarray) -> List[Tuple[int, Point]]:
    anchors = [(_index_for_time(times, p.time), p) for p in drawing.points]
    return sorted(anchors, key=lambda a: a[0])


class _LineModel:
    """A level that may slope: value(i) = base + slope * (i - i0)."""

    def __init__(self, i0: int, price0: float, slope: float) -> None:
        self.i0 = i0
        self.price0 = price0
        self.slope = slope

    def value(self, i: int) -> float:
        return self.price0 + self.slope * (i - self.i0)


def _build_line(anchors: List[Tuple[int, Point]]) -> Optional[_LineModel]:
    if not anchors:
        return None
    if len(anchors) == 1:
        return _LineModel(anchors[0][0], anchors[0][1].price, 0.0)
    (i0, p0), (i1, p1) = anchors[0], anchors[-1]
    span = max(i1 - i0, 1)
    return _LineModel(i0, p0.price, (p1.price - p0.price) / span)


# ── touch / break scanning ────────────────────────────────────

def _scan_line(df: pd.DataFrame, line: _LineModel, start_index: int, atr: float,
               instrument: Instrument, treat_as: str) -> Tuple[List[TouchEvent], Dict[str, int]]:
    """Walk bars from the drawing's origin and classify every interaction."""
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    opens = df["open"].to_numpy()
    volumes = df["volume"].to_numpy()
    times = df["time"].to_numpy()
    n = len(df)

    avg_volume = float(np.mean(volumes[-50:])) if n else 1.0
    avg_volume = avg_volume if avg_volume > 0 else 1.0
    tol = max(atr * TOUCH_TOLERANCE_ATR, 1e-12)
    break_tol = max(atr * BREAK_TOLERANCE_ATR, 1e-12)

    events: List[TouchEvent] = []
    counts = {"touches": 0, "reactions": 0, "breaks": 0, "fake_breaks": 0, "retests": 0}
    broken_side: Optional[str] = None
    last_event_index = -99

    for i in range(max(start_index, 1), n):
        level = line.value(i)
        if not math.isfinite(level) or level <= 0:
            continue

        high, low, close, open_ = highs[i], lows[i], closes[i], opens[i]
        straddles = low - tol <= level <= high + tol
        if not straddles:
            continue
        # One event per cluster of bars — a 5-bar hug is one test, not five.
        if i - last_event_index < 2:
            continue

        vol_ratio = round(float(volumes[i] / avg_volume), 2)
        above_close = close > level + break_tol
        below_close = close < level - break_tol

        # Reaction: how far price travelled away from the level afterwards.
        forward = slice(i + 1, min(i + 1 + REACTION_WINDOW, n))
        fwd_high = float(highs[forward].max()) if i + 1 < n else high
        fwd_low = float(lows[forward].min()) if i + 1 < n else low

        if treat_as == "resistance":
            reaction = level - fwd_low
            wick = high - max(open_, close)
            broke = above_close
            faked = high > level + tol and not above_close
        else:
            reaction = fwd_high - level
            wick = min(open_, close) - low
            broke = below_close
            faked = low < level - tol and not below_close

        reaction_pips = round(instrument.to_pips(max(reaction, 0.0)), 1)
        wick_ratio = round(float(wick / max(high - low, 1e-12)), 2)

        if broke:
            kind = "break"
            counts["breaks"] += 1
            broken_side = "above" if treat_as == "resistance" else "below"
        elif faked:
            kind = "fake_break"
            counts["fake_breaks"] += 1
            counts["touches"] += 1
        elif broken_side is not None:
            kind = "retest"
            counts["retests"] += 1
            counts["touches"] += 1
        else:
            kind = "touch"
            counts["touches"] += 1

        if kind != "break" and reaction > atr * 0.75:
            counts["reactions"] += 1
            if kind == "touch":
                kind = "rejection"

        events.append(TouchEvent(
            time=int(times[i]), price=round(float(level), instrument.digits), index=i,
            kind=kind,
            distance_pips=round(instrument.to_pips(min(abs(high - level), abs(low - level))), 1),
            body_close_beyond=bool(above_close or below_close),
            volume_ratio=vol_ratio,
            reaction_pips=reaction_pips,
            wick_ratio=wick_ratio,
        ))
        last_event_index = i

    return events, counts


def _scan_zone(df: pd.DataFrame, top: float, bottom: float, start_index: int,
               atr: float, instrument: Instrument
               ) -> Tuple[List[TouchEvent], Dict[str, int]]:
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    volumes = df["volume"].to_numpy()
    times = df["time"].to_numpy()
    n = len(df)
    avg_volume = float(np.mean(volumes[-50:])) or 1.0

    events: List[TouchEvent] = []
    counts = {"touches": 0, "reactions": 0, "breaks": 0, "fake_breaks": 0, "retests": 0}
    last_event_index = -99

    for i in range(max(start_index, 1), n):
        entered = lows[i] <= top and highs[i] >= bottom
        if not entered or i - last_event_index < 2:
            continue

        through = closes[i] > top or closes[i] < bottom
        forward = slice(i + 1, min(i + 1 + REACTION_WINDOW, n))
        fwd_high = float(highs[forward].max()) if i + 1 < n else highs[i]
        fwd_low = float(lows[forward].min()) if i + 1 < n else lows[i]
        reaction = max(fwd_high - top, bottom - fwd_low, 0.0)

        if through and (closes[i] > top + atr * 0.25 or closes[i] < bottom - atr * 0.25):
            kind = "break"
            counts["breaks"] += 1
        else:
            kind = "touch"
            counts["touches"] += 1
            if reaction > atr * 0.75:
                counts["reactions"] += 1
                kind = "rejection"

        events.append(TouchEvent(
            time=int(times[i]), price=round(float((top + bottom) / 2), instrument.digits),
            index=i, kind=kind,
            distance_pips=0.0,
            body_close_beyond=bool(through),
            volume_ratio=round(float(volumes[i] / avg_volume), 2),
            reaction_pips=round(instrument.to_pips(reaction), 1),
        ))
        last_event_index = i

    return events, counts


# ── scoring ───────────────────────────────────────────────────

def _strength(counts: Dict[str, int], age_bars: int, last_touch_bars_ago: Optional[int],
              avg_volume_ratio: float) -> float:
    score = 20.0
    score += min(counts["touches"], 6) * 8.0
    score += min(counts["reactions"], 4) * 7.0
    score += min(counts["fake_breaks"], 3) * 5.0        # stop hunts confirm the level matters
    score -= counts["breaks"] * 14.0
    score += min(counts["retests"], 3) * 4.0
    if age_bars > 60:
        score += 6.0
    if last_touch_bars_ago is not None and last_touch_bars_ago < 20:
        score += 5.0
    score += (avg_volume_ratio - 1.0) * 8.0
    return round(max(0.0, min(100.0, score)), 1)


def _validity(counts: Dict[str, int], events: List[TouchEvent]) -> str:
    if not events:
        return "untested"
    last_break = next((e for e in reversed(events) if e.kind == "break"), None)
    if last_break is not None:
        later = [e for e in events if e.index > last_break.index]
        if any(e.kind in ("retest", "rejection") for e in later):
            return "broken"
        return "invalidated" if counts["breaks"] > counts["reactions"] else "broken"
    if counts["reactions"] >= 1 or counts["touches"] >= 2:
        return "respected"
    return "untested"


# ── main entry ────────────────────────────────────────────────

def analyse_drawing(drawing: Drawing, df: pd.DataFrame, instrument: Instrument,
                    atr: float) -> DrawingAnalytics:
    result = DrawingAnalytics(drawing_id=drawing.id, type=drawing.type)
    if df is None or df.empty or not drawing.points:
        result.notes.append("No price context available for this drawing")
        return result

    times = df["time"].to_numpy()
    n = len(df)
    last_index = n - 1
    price = float(df["close"].iloc[-1])
    atr = max(atr, 1e-9)

    anchors = _sorted_anchors(drawing, times)
    result.anchor_start = anchors[0][1]
    result.anchor_end = anchors[-1][1]
    result.length_bars = max(anchors[-1][0] - anchors[0][0], 0)
    result.age_bars = max(last_index - anchors[0][0], 0)

    dtype = drawing.type

    # ── fibonacci ────────────────────────────────────────────
    if dtype in (DrawingType.FIB_RETRACEMENT, DrawingType.FIB_EXTENSION):
        if len(anchors) < 2:
            result.notes.append("Fibonacci needs two anchors")
            return result
        p0, p1 = anchors[0][1].price, anchors[-1][1].price
        span = p1 - p0
        ratios = (FIB_RETRACEMENT if dtype == DrawingType.FIB_RETRACEMENT else FIB_EXTENSION)
        levels = {"{:.3f}".format(r): round(p1 - span * r, instrument.digits)
                  for r in ratios} if dtype == DrawingType.FIB_RETRACEMENT else {
            "{:.3f}".format(r): round(p0 + span * r, instrument.digits) for r in ratios}
        result.fib_levels = levels
        result.role = "measurement"
        nearest_key = min(levels, key=lambda k: abs(levels[k] - price))
        nearest = levels[nearest_key]
        result.current_value = nearest
        result.distance_pips = round(instrument.to_pips(abs(price - nearest)), 1)
        result.distance_atr = round(abs(price - nearest) / atr, 2)
        result.approaching = result.distance_atr < 0.6
        golden = levels.get("0.618") or levels.get("1.618")
        if golden is not None:
            result.notes.append("Golden ratio at {:.5f}".format(golden))
        result.notes.append(
            "Price sitting closest to the {} level ({:.5f}), {:.1f} pips away".format(
                nearest_key, nearest, result.distance_pips))
        result.strength = 45.0
        return result

    # ── risk / reward box ────────────────────────────────────
    if dtype == DrawingType.RISK_REWARD:
        prices = [p.price for _, p in anchors]
        if len(prices) < 2:
            return result
        entry = prices[0]
        stop = min(prices) if prices[-1] > entry else max(prices)
        target = max(prices) if prices[-1] > entry else min(prices)
        risk = abs(entry - stop)
        reward = abs(target - entry)
        result.role = "measurement"
        result.current_value = entry
        result.upper_value = max(prices)
        result.lower_value = min(prices)
        result.notes.append(
            "Manual plan — risk {:.1f} pips, reward {:.1f} pips, R:R {:.2f}".format(
                instrument.to_pips(risk), instrument.to_pips(reward),
                (reward / risk) if risk else 0.0))
        result.strength = 40.0
        return result

    # ── annotations ──────────────────────────────────────────
    if dtype in (DrawingType.ARROW, DrawingType.TEXT, DrawingType.BRUSH):
        result.role = "annotation"
        result.current_value = anchors[-1][1].price
        result.distance_pips = round(instrument.to_pips(abs(price - result.current_value)), 1)
        if drawing.text:
            result.notes.append('User note: "{}"'.format(drawing.text[:180]))
        elif dtype == DrawingType.ARROW:
            direction = "up" if anchors[-1][1].price > anchors[0][1].price else "down"
            result.notes.append("User marked a directional bias: {}".format(direction))
        return result

    # ── zones ────────────────────────────────────────────────
    if dtype in ZONE_TYPES:
        prices = [p.price for _, p in anchors]
        top, bottom = max(prices), min(prices)
        result.upper_value = round(top, instrument.digits)
        result.lower_value = round(bottom, instrument.digits)
        result.current_value = round((top + bottom) / 2, instrument.digits)
        result.role = ("supply" if dtype == DrawingType.SUPPLY_ZONE
                       else "demand" if dtype == DrawingType.DEMAND_ZONE else "zone")

        events, counts = _scan_zone(df, top, bottom, anchors[0][0], atr, instrument)
        result.events = events[-12:]
        result.touches = counts["touches"]
        result.reactions = counts["reactions"]
        result.breaks = counts["breaks"]

        if price > top:
            result.price_side = "above"
            distance = price - top
        elif price < bottom:
            result.price_side = "below"
            distance = bottom - price
        else:
            result.price_side = "inside"
            distance = 0.0

        result.distance_pips = round(instrument.to_pips(distance), 1)
        result.distance_atr = round(distance / atr, 2)
        result.approaching = result.distance_atr < 0.75
        vol_ratio = (float(np.mean([e.volume_ratio for e in events])) if events else 1.0)
        result.volume_at_touches = round(vol_ratio, 2)
        result.last_touch_bars_ago = (last_index - events[-1].index) if events else None
        result.strength = _strength(counts, result.age_bars, result.last_touch_bars_ago, vol_ratio)
        result.validity = _validity(counts, events)

        if result.price_side == "inside":
            result.notes.append("Price is trading inside the zone right now")
        elif result.approaching:
            result.notes.append("Price approaching the zone from {} — {:.1f} pips away".format(
                result.price_side, result.distance_pips))
        if counts["reactions"]:
            result.notes.append("Zone produced {} clean reaction(s)".format(counts["reactions"]))
        if counts["breaks"]:
            result.notes.append("Zone has been closed through {} time(s)".format(counts["breaks"]))
        return result

    # ── lines, levels, channels ──────────────────────────────
    if dtype not in LEVEL_TYPES:
        result.notes.append("Unsupported drawing type for analytics")
        return result

    line = _build_line(anchors)
    if line is None:
        return result

    channel_offset = 0.0
    if dtype in (DrawingType.CHANNEL, DrawingType.PARALLEL_CHANNEL) and len(anchors) >= 3:
        third_index, third_point = anchors[1] if len(anchors) == 3 else anchors[-1]
        # The parallel rail is the anchor furthest from the base line.
        offsets = [p.price - line.value(i) for i, p in anchors]
        channel_offset = max(offsets, key=abs)

    current = line.value(last_index)
    result.current_value = round(current, instrument.digits)
    result.slope_per_bar = round(line.slope, 10)
    result.angle_degrees = round(math.degrees(math.atan(line.slope / atr)), 2)

    if dtype == DrawingType.SUPPORT:
        role = "support"
    elif dtype == DrawingType.RESISTANCE:
        role = "resistance"
    else:
        role = "support" if price >= current else "resistance"
    result.role = role
    result.price_side = "above" if price >= current else "below"

    events, counts = _scan_line(df, line, anchors[0][0], atr, instrument, role)
    result.events = events[-12:]
    result.touches = counts["touches"]
    result.reactions = counts["reactions"]
    result.breaks = counts["breaks"]
    result.fake_breaks = counts["fake_breaks"]
    result.retests = counts["retests"]

    distance = abs(price - current)
    result.distance_pips = round(instrument.to_pips(distance), 1)
    result.distance_atr = round(distance / atr, 2)

    recent_closes = df["close"].to_numpy()[-4:]
    if len(recent_closes) >= 2:
        moving_up = recent_closes[-1] > recent_closes[0]
        toward = (moving_up and price < current) or (not moving_up and price > current)
        result.approaching = bool(toward and result.distance_atr < 1.0)

    if channel_offset:
        upper = current + max(channel_offset, 0.0)
        lower = current + min(channel_offset, 0.0)
        result.upper_value = round(max(upper, current), instrument.digits)
        result.lower_value = round(min(lower, current), instrument.digits)
        width = abs(channel_offset)
        position = (price - min(upper, current)) / width if width else 0.5
        result.notes.append(
            "Price is at {:.0f}% of channel width".format(max(0.0, min(1.0, position)) * 100))

    vol_ratio = float(np.mean([e.volume_ratio for e in events])) if events else 1.0
    result.volume_at_touches = round(vol_ratio, 2)
    result.last_touch_bars_ago = (last_index - events[-1].index) if events else None
    result.strength = _strength(counts, result.age_bars, result.last_touch_bars_ago, vol_ratio)
    result.validity = _validity(counts, events)

    slope_desc = ("rising" if line.slope > 0 else "falling" if line.slope < 0 else "horizontal")
    result.notes.append(
        "{} {} drawn across {} bars, currently projecting {:.5f}".format(
            slope_desc.title(), role, result.length_bars, current))
    if counts["touches"]:
        result.notes.append(
            "Tested {} time(s) with {} clean reaction(s)".format(
                counts["touches"], counts["reactions"]))
    if counts["fake_breaks"]:
        result.notes.append(
            "{} failed break(s) — wicks through, closes back inside".format(counts["fake_breaks"]))
    if counts["breaks"]:
        last_break = next((e for e in reversed(events) if e.kind == "break"), None)
        if last_break:
            result.notes.append(
                "Closed through {} bars ago; level now flipped".format(last_index - last_break.index))
    if result.approaching:
        result.notes.append(
            "Price is approaching from {} — {:.1f} pips ({:.2f} ATR) away".format(
                result.price_side, result.distance_pips, result.distance_atr))
    if events and events[-1].volume_ratio > 1.4:
        result.notes.append(
            "Last interaction came on {:.1f}x average volume".format(events[-1].volume_ratio))

    return result


def analyse_many(drawings: List[Drawing], df: pd.DataFrame, instrument: Instrument,
                 atr: float) -> List[DrawingAnalytics]:
    out: List[DrawingAnalytics] = []
    for drawing in drawings:
        if not drawing.visible:
            continue
        try:
            out.append(analyse_drawing(drawing, df, instrument, atr))
        except Exception as exc:  # noqa: BLE001 - one bad drawing must not kill analysis
            out.append(DrawingAnalytics(
                drawing_id=drawing.id, type=drawing.type,
                notes=["Could not analyse this drawing: {}".format(exc)],
            ))
    return out


def summarise(analytics: List[DrawingAnalytics], digits: int = 5) -> List[Dict[str, object]]:
    """Compact form for the AI brief."""
    out = []
    for a in analytics:
        if a.role == "annotation" and not a.notes:
            continue
        entry: Dict[str, object] = {
            "type": a.type.value,
            "role": a.role,
            "level": a.current_value,
            "strength": a.strength,
            "validity": a.validity,
            "touches": a.touches,
            "reactions": a.reactions,
            "breaks": a.breaks,
            "fake_breaks": a.fake_breaks,
            "distance_pips": a.distance_pips,
            "distance_atr": a.distance_atr,
            "approaching": a.approaching,
            "price_side": a.price_side,
            "angle_degrees": a.angle_degrees,
            "age_bars": a.age_bars,
            "notes": a.notes[:4],
        }
        if a.upper_value is not None:
            entry["zone"] = {"top": a.upper_value, "bottom": a.lower_value}
        if a.fib_levels:
            entry["fib_levels"] = a.fib_levels
        out.append(entry)
    return out
