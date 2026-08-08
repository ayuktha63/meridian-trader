"""Market structure & Smart Money Concepts.

Definitions used here (stated explicitly because every desk words them
slightly differently):

* **Swing point** — fractal extreme with `left` bars lower/higher on the left
  and `right` on the right. A swing is only *confirmed* `right` bars after it
  printed; the engine never uses unconfirmed swings for structure decisions.
* **BOS** — a candle *closes* beyond the most recent unbroken swing in the
  direction of the prevailing trend. Continuation.
* **CHoCH** — the same break, but against the prevailing trend. The first
  warning that control has changed hands.
* **Liquidity sweep** — a *wick* takes out a prior swing while the body closes
  back inside. Stops were taken, direction was not.
* **FVG** — three-candle imbalance: `low[i] > high[i-2]` (bullish) or
  `high[i] < low[i-2]` (bearish).
* **Order block** — last opposing candle before the impulse that produced a
  BOS. **Breaker** — an order block price later closed through, now expected
  to act inverted. **Mitigation** — an order block price has returned into
  and respected.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..instruments import Instrument
from ..schemas.analysis import Bias, MarketStructure, StructureEvent, SwingPoint, Zone

MAX_ZONES = 8


# ── swings ────────────────────────────────────────────────────

def find_swings(df: pd.DataFrame, left: int = 3, right: int = 3) -> List[SwingPoint]:
    n = len(df)
    if n < left + right + 1:
        return []

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    times = df["time"].to_numpy()
    swings: List[SwingPoint] = []

    for i in range(left, n - right):
        window_hi = highs[i - left:i + right + 1]
        window_lo = lows[i - left:i + right + 1]
        if highs[i] == window_hi.max() and (highs[i] > highs[i - left:i].max()):
            strength = int(np.sum(window_hi < highs[i]))
            swings.append(SwingPoint(index=i, time=int(times[i]), price=float(highs[i]),
                                     kind="swing_high", strength=strength))
        if lows[i] == window_lo.min() and (lows[i] < lows[i - left:i].min()):
            strength = int(np.sum(window_lo > lows[i]))
            swings.append(SwingPoint(index=i, time=int(times[i]), price=float(lows[i]),
                                     kind="swing_low", strength=strength))

    swings.sort(key=lambda s: s.index)
    return _label_swings(swings)


def _label_swings(swings: List[SwingPoint]) -> List[SwingPoint]:
    last_high: Optional[float] = None
    last_low: Optional[float] = None
    for swing in swings:
        if swing.kind == "swing_high":
            if last_high is not None:
                swing.label = "HH" if swing.price > last_high else "LH"
            last_high = swing.price
        else:
            if last_low is not None:
                swing.label = "HL" if swing.price > last_low else "LL"
            last_low = swing.price
    return swings


# ── structure events ──────────────────────────────────────────

def detect_structure_events(
    df: pd.DataFrame, swings: List[SwingPoint], right: int = 3, atr: Optional[float] = None
) -> Tuple[List[StructureEvent], Bias]:
    """Walk the series forward emitting BOS / CHoCH / liquidity sweeps."""
    if not swings:
        return [], Bias.NEUTRAL

    n = len(df)
    closes = df["close"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    opens = df["open"].to_numpy()
    times = df["time"].to_numpy()
    atr = atr or float(np.nanmean(highs - lows)) or 1e-6

    pending = sorted(swings, key=lambda s: s.index + right)
    cursor = 0
    active_high: Optional[SwingPoint] = None
    active_low: Optional[SwingPoint] = None
    events: List[StructureEvent] = []
    trend = Bias.NEUTRAL

    for i in range(n):
        while cursor < len(pending) and pending[cursor].index + right <= i:
            swing = pending[cursor]
            if swing.kind == "swing_high":
                if active_high is None or swing.price >= active_high.price or swing.index > active_high.index:
                    active_high = swing
            else:
                if active_low is None or swing.price <= active_low.price or swing.index > active_low.index:
                    active_low = swing
            cursor += 1

        if active_high is not None and i > active_high.index:
            level = active_high.price
            if closes[i] > level:
                kind = "BOS" if trend == Bias.BULLISH else "CHoCH"
                significance = min(1.0, abs(closes[i] - level) / (atr + 1e-9))
                events.append(StructureEvent(
                    kind=kind, direction="bullish", time=int(times[i]), index=i,
                    price=float(closes[i]), reference_price=float(level),
                    significance=round(0.4 + 0.6 * significance, 3),
                    note="Close above swing high @ {:.5f}".format(level),
                ))
                trend = Bias.BULLISH
                active_high = None
            elif highs[i] > level and closes[i] < level and (closes[i] < opens[i] or highs[i] - level > 0.15 * atr):
                events.append(StructureEvent(
                    kind="liquidity_sweep", direction="bearish", time=int(times[i]), index=i,
                    price=float(highs[i]), reference_price=float(level),
                    significance=round(min(1.0, (highs[i] - level) / (atr + 1e-9)), 3),
                    note="Buy-side liquidity taken above {:.5f}, closed back inside".format(level),
                ))

        if active_low is not None and i > active_low.index:
            level = active_low.price
            if closes[i] < level:
                kind = "BOS" if trend == Bias.BEARISH else "CHoCH"
                significance = min(1.0, abs(level - closes[i]) / (atr + 1e-9))
                events.append(StructureEvent(
                    kind=kind, direction="bearish", time=int(times[i]), index=i,
                    price=float(closes[i]), reference_price=float(level),
                    significance=round(0.4 + 0.6 * significance, 3),
                    note="Close below swing low @ {:.5f}".format(level),
                ))
                trend = Bias.BEARISH
                active_low = None
            elif lows[i] < level and closes[i] > level and (closes[i] > opens[i] or level - lows[i] > 0.15 * atr):
                events.append(StructureEvent(
                    kind="liquidity_sweep", direction="bullish", time=int(times[i]), index=i,
                    price=float(lows[i]), reference_price=float(level),
                    significance=round(min(1.0, (level - lows[i]) / (atr + 1e-9)), 3),
                    note="Sell-side liquidity taken below {:.5f}, closed back inside".format(level),
                ))

    for event in events:
        event.bars_ago = n - 1 - event.index
    return events, trend


# ── imbalances ────────────────────────────────────────────────

def find_fair_value_gaps(df: pd.DataFrame, atr: float, min_size_atr: float = 0.12,
                         limit: int = MAX_ZONES) -> List[Zone]:
    n = len(df)
    if n < 3:
        return []
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    times = df["time"].to_numpy()
    price = float(closes[-1])
    gaps: List[Zone] = []

    for i in range(2, n):
        bull_gap = lows[i] - highs[i - 2]
        bear_gap = lows[i - 2] - highs[i]
        for size, direction, top, bottom in (
            (bull_gap, "bullish", lows[i], highs[i - 2]),
            (bear_gap, "bearish", lows[i - 2], highs[i]),
        ):
            if size <= max(atr * min_size_atr, 1e-9):
                continue
            future_lows = lows[i + 1:]
            future_highs = highs[i + 1:]
            if direction == "bullish":
                filled = bool(len(future_lows) and future_lows.min() <= bottom)
                tested = int(np.sum(future_lows <= top)) if len(future_lows) else 0
            else:
                filled = bool(len(future_highs) and future_highs.max() >= top)
                tested = int(np.sum(future_highs >= bottom)) if len(future_highs) else 0
            gaps.append(Zone(
                kind="fvg", direction=direction,
                top=float(top), bottom=float(bottom),
                start_time=int(times[i - 2]), end_time=int(times[-1]),
                start_index=i - 2, end_index=i,
                mitigated=filled, tested=tested,
                strength=round(min(1.0, size / (atr + 1e-9)), 3),
                distance_pct=round(abs(price - (top + bottom) / 2) / price * 100, 3),
                note="{} imbalance {:.1f}% of ATR".format(direction.title(), size / (atr + 1e-9) * 100),
            ))

    unfilled = [g for g in gaps if not g.mitigated]
    unfilled.sort(key=lambda g: (g.distance_pct, -g.strength))
    return unfilled[:limit] or gaps[-limit:]


# ── order blocks ──────────────────────────────────────────────

def find_order_blocks(df: pd.DataFrame, events: List[StructureEvent], atr: float,
                      lookback: int = 25, limit: int = MAX_ZONES
                      ) -> Tuple[List[Zone], List[Zone], List[Zone]]:
    """Returns (order_blocks, breaker_blocks, mitigation_blocks)."""
    n = len(df)
    if n < 5:
        return [], [], []

    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    times = df["time"].to_numpy()
    price = float(closes[-1])

    blocks: List[Zone] = []
    breakers: List[Zone] = []
    mitigations: List[Zone] = []
    seen: set = set()

    for event in events:
        if event.kind not in ("BOS", "CHoCH"):
            continue
        i = event.index
        want_bearish_candle = event.direction == "bullish"
        origin = None
        for j in range(i - 1, max(i - lookback, 0) - 1, -1):
            is_bearish = closes[j] < opens[j]
            if is_bearish == want_bearish_candle:
                origin = j
                break
        if origin is None or origin in seen:
            continue
        seen.add(origin)

        top, bottom = float(highs[origin]), float(lows[origin])
        after_lows = lows[origin + 1:]
        after_highs = highs[origin + 1:]
        after_closes = closes[origin + 1:]

        if event.direction == "bullish":
            tested = int(np.sum(after_lows <= top)) if len(after_lows) else 0
            violated = bool(len(after_closes) and after_closes.min() < bottom)
        else:
            tested = int(np.sum(after_highs >= bottom)) if len(after_highs) else 0
            violated = bool(len(after_closes) and after_closes.max() > top)

        zone = Zone(
            kind="order_block", direction=event.direction,
            top=top, bottom=bottom,
            start_time=int(times[origin]), end_time=int(times[-1]),
            start_index=origin, end_index=i,
            mitigated=tested > 0, tested=tested,
            strength=round(min(1.0, event.significance * (1.0 + 0.15 * min(tested, 3))), 3),
            distance_pct=round(abs(price - (top + bottom) / 2) / price * 100, 3),
            note="Origin of {} {}".format(event.direction, event.kind),
        )

        if violated:
            zone.kind = "breaker"
            zone.direction = "bearish" if event.direction == "bullish" else "bullish"
            zone.note = "Failed {} order block — now a breaker".format(event.direction)
            breakers.append(zone)
        elif tested > 0:
            mitigation = zone.model_copy(deep=True)
            mitigation.kind = "mitigation"
            mitigation.note = "Order block revisited {}x and held".format(tested)
            mitigations.append(mitigation)
            blocks.append(zone)
        else:
            blocks.append(zone)

    key = lambda z: z.distance_pct  # noqa: E731
    return (sorted(blocks, key=key)[:limit],
            sorted(breakers, key=key)[:limit],
            sorted(mitigations, key=key)[:limit])


# ── supply / demand ───────────────────────────────────────────

def find_supply_demand(df: pd.DataFrame, atr: float, impulse_atr: float = 1.8,
                       limit: int = 6) -> Tuple[List[Zone], List[Zone]]:
    """Base-then-impulse zones: a tight consolidation that price left violently."""
    n = len(df)
    if n < 12:
        return [], []

    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    times = df["time"].to_numpy()
    price = float(closes[-1])

    supply: List[Zone] = []
    demand: List[Zone] = []

    i = 2
    while i < n - 4:
        base_end = i
        base_top, base_bottom = highs[i], lows[i]
        # Extend the base while candles stay small and overlapping.
        while (base_end + 1 < n - 3 and base_end - i < 4
               and abs(closes[base_end + 1] - opens[base_end + 1]) < 0.55 * atr):
            base_end += 1
            base_top = max(base_top, highs[base_end])
            base_bottom = min(base_bottom, lows[base_end])

        leg = closes[min(base_end + 3, n - 1)] - closes[base_end]
        if abs(leg) >= impulse_atr * atr and (base_top - base_bottom) < 2.2 * atr:
            direction = "bullish" if leg > 0 else "bearish"
            after = (lows[base_end + 1:] if direction == "bullish" else highs[base_end + 1:])
            if direction == "bullish":
                tested = int(np.sum(after <= base_top)) if len(after) else 0
                broken = bool(len(after) and after.min() < base_bottom)
            else:
                tested = int(np.sum(after >= base_bottom)) if len(after) else 0
                broken = bool(len(after) and after.max() > base_top)

            zone = Zone(
                kind="demand" if direction == "bullish" else "supply",
                direction=direction,
                top=float(base_top), bottom=float(base_bottom),
                start_time=int(times[i]), end_time=int(times[-1]),
                start_index=i, end_index=base_end,
                mitigated=broken, tested=tested,
                strength=round(min(1.0, abs(leg) / (atr * 3.0)), 3),
                distance_pct=round(abs(price - (base_top + base_bottom) / 2) / price * 100, 3),
                note="{:.1f} ATR departure from base".format(abs(leg) / (atr + 1e-9)),
            )
            (demand if direction == "bullish" else supply).append(zone)
            i = base_end + 3
        else:
            i += 1

    fresh = lambda zones: sorted(  # noqa: E731
        [z for z in zones if not z.mitigated] or zones, key=lambda z: z.distance_pct
    )[:limit]
    return fresh(supply), fresh(demand)


# ── liquidity ─────────────────────────────────────────────────

def find_equal_levels(swings: List[SwingPoint], atr: float,
                      tolerance_atr: float = 0.10) -> Tuple[List[float], List[float]]:
    tol = atr * tolerance_atr

    def cluster(points: List[float]) -> List[float]:
        out: List[float] = []
        used = [False] * len(points)
        for a in range(len(points)):
            if used[a]:
                continue
            group = [points[a]]
            for b in range(a + 1, len(points)):
                if not used[b] and abs(points[b] - points[a]) <= tol:
                    group.append(points[b])
                    used[b] = True
            if len(group) >= 2:
                out.append(round(float(np.mean(group)), 8))
        return out

    highs = [s.price for s in swings if s.kind == "swing_high"][-14:]
    lows = [s.price for s in swings if s.kind == "swing_low"][-14:]
    return cluster(highs), cluster(lows)


def build_liquidity_pools(df: pd.DataFrame, swings: List[SwingPoint],
                          equal_highs: List[float], equal_lows: List[float],
                          atr: float, limit: int = 6) -> List[Zone]:
    if df.empty:
        return []
    price = float(df["close"].iloc[-1])
    last_time = int(df["time"].iloc[-1])
    first_time = int(df["time"].iloc[0])
    pools: List[Zone] = []

    def add(level: float, direction: str, note: str, strength: float) -> None:
        pools.append(Zone(
            kind="liquidity_pool", direction=direction,
            top=float(level + atr * 0.12), bottom=float(level - atr * 0.12),
            start_time=first_time, end_time=last_time,
            start_index=0, end_index=len(df) - 1,
            strength=round(strength, 3),
            distance_pct=round(abs(price - level) / price * 100, 3),
            note=note,
        ))

    for level in equal_highs:
        add(level, "bearish", "Equal highs — resting buy-side liquidity", 0.85)
    for level in equal_lows:
        add(level, "bullish", "Equal lows — resting sell-side liquidity", 0.85)

    untouched_highs = [s for s in swings if s.kind == "swing_high" and s.price > price][-3:]
    untouched_lows = [s for s in swings if s.kind == "swing_low" and s.price < price][-3:]
    for swing in untouched_highs:
        add(swing.price, "bearish", "Swing high stops above", 0.55)
    for swing in untouched_lows:
        add(swing.price, "bullish", "Swing low stops below", 0.55)

    pools.sort(key=lambda z: (z.distance_pct, -z.strength))
    return pools[:limit]


# ── support / resistance clustering ───────────────────────────

def cluster_levels(swings: List[SwingPoint], price: float, atr: float,
                   tolerance_atr: float = 0.45, top_n: int = 6
                   ) -> Tuple[List[float], List[float]]:
    if not swings:
        return [], []
    tol = max(atr * tolerance_atr, 1e-9)
    points = sorted(s.price for s in swings)

    clusters: List[List[float]] = []
    for p in points:
        if clusters and abs(p - clusters[-1][-1]) <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])

    scored = [(float(np.mean(c)), len(c)) for c in clusters]
    scored.sort(key=lambda x: (-x[1], abs(x[0] - price)))

    support = sorted([lvl for lvl, _ in scored if lvl < price],
                     key=lambda l: price - l)[:top_n]
    resistance = sorted([lvl for lvl, _ in scored if lvl >= price],
                        key=lambda l: l - price)[:top_n]
    return [round(l, 8) for l in support], [round(l, 8) for l in resistance]


# ── orchestration ─────────────────────────────────────────────

def analyse(df: pd.DataFrame, instrument: Instrument, atr_value: Optional[float] = None,
            left: int = 3, right: int = 3) -> MarketStructure:
    result = MarketStructure()
    if df is None or len(df) < 20:
        result.notes.append("Insufficient history for structural analysis")
        return result

    atr = atr_value or float((df["high"] - df["low"]).tail(50).mean()) or 1e-6
    price = float(df["close"].iloc[-1])

    swings = find_swings(df, left, right)
    result.swings = swings[-40:]

    events, trend = detect_structure_events(df, swings, right, atr)
    result.events = events[-25:]
    result.trend = trend

    bos = [e for e in events if e.kind == "BOS"]
    choch = [e for e in events if e.kind == "CHoCH"]
    result.last_bos = bos[-1] if bos else None
    result.last_choch = choch[-1] if choch else None

    highs = [s for s in swings if s.kind == "swing_high"]
    lows = [s for s in swings if s.kind == "swing_low"]
    recent_labels = [s.label for s in swings[-6:] if s.label]
    bull_labels = sum(1 for l in recent_labels if l in ("HH", "HL"))
    bear_labels = sum(1 for l in recent_labels if l in ("LH", "LL"))

    if bull_labels >= 3 and bull_labels > bear_labels:
        result.structure_label = "uptrend"
        result.trend = Bias.BULLISH
    elif bear_labels >= 3 and bear_labels > bull_labels:
        result.structure_label = "downtrend"
        result.trend = Bias.BEARISH
    elif result.last_choch and result.last_choch.bars_ago < 12:
        result.structure_label = "transition"
        result.trend = (Bias.BULLISH if result.last_choch.direction == "bullish"
                        else Bias.BEARISH)
    else:
        result.structure_label = "ranging"
        if bull_labels == bear_labels:
            result.trend = Bias.NEUTRAL

    result.order_blocks, result.breaker_blocks, result.mitigation_blocks = find_order_blocks(
        df, events, atr
    )
    result.fair_value_gaps = find_fair_value_gaps(df, atr)
    result.supply_zones, result.demand_zones = find_supply_demand(df, atr)
    result.equal_highs, result.equal_lows = find_equal_levels(swings, atr)
    result.liquidity_pools = build_liquidity_pools(
        df, swings, result.equal_highs, result.equal_lows, atr
    )

    window = df.tail(120)
    range_high = float(window["high"].max())
    range_low = float(window["low"].min())
    if highs and lows:
        range_high = max(range_high, float(highs[-1].price))
        range_low = min(range_low, float(lows[-1].price))
    result.range_high = round(range_high, instrument.digits)
    result.range_low = round(range_low, instrument.digits)
    equilibrium = (range_high + range_low) / 2.0
    result.equilibrium = round(equilibrium, instrument.digits)

    span = max(range_high - range_low, 1e-9)
    position = (price - range_low) / span
    if position > 0.62:
        result.premium_discount = "premium"
    elif position < 0.38:
        result.premium_discount = "discount"
    else:
        result.premium_discount = "equilibrium"

    result.support_levels, result.resistance_levels = cluster_levels(swings, price, atr)

    sweeps = [e for e in events if e.kind == "liquidity_sweep" and e.bars_ago <= 10]
    if sweeps:
        latest = sweeps[-1]
        result.notes.append(
            "{} liquidity sweep {} bars ago at {:.5f}".format(
                latest.direction, latest.bars_ago, latest.price)
        )
    if result.last_choch and result.last_choch.bars_ago <= 6:
        result.notes.append(
            "Fresh {} CHoCH — prior structure invalidated".format(result.last_choch.direction)
        )
    if result.premium_discount == "premium":
        result.notes.append("Price in premium — longs are late, shorts have location")
    elif result.premium_discount == "discount":
        result.notes.append("Price in discount — longs have location")

    return result


def summarise(structure: MarketStructure, digits: int = 5) -> Dict[str, object]:
    """Compact structural evidence for the AI brief."""
    fmt = "{:." + str(digits) + "f}"
    return {
        "trend": structure.trend.value,
        "structure": structure.structure_label,
        "recent_swings": [
            {"label": s.label, "kind": s.kind, "price": round(s.price, digits)}
            for s in structure.swings[-6:]
        ],
        "last_bos": (
            "{} BOS {} bars ago at {}".format(
                structure.last_bos.direction, structure.last_bos.bars_ago,
                fmt.format(structure.last_bos.price))
            if structure.last_bos else None
        ),
        "last_choch": (
            "{} CHoCH {} bars ago at {}".format(
                structure.last_choch.direction, structure.last_choch.bars_ago,
                fmt.format(structure.last_choch.price))
            if structure.last_choch else None
        ),
        "recent_sweeps": [
            "{} sweep {} bars ago".format(e.direction, e.bars_ago)
            for e in structure.events if e.kind == "liquidity_sweep" and e.bars_ago <= 15
        ][-3:],
        "order_blocks": [
            {"direction": z.direction, "top": round(z.top, digits),
             "bottom": round(z.bottom, digits), "distance_pct": z.distance_pct,
             "tested": z.tested}
            for z in structure.order_blocks[:3]
        ],
        "breaker_blocks": [
            {"direction": z.direction, "top": round(z.top, digits),
             "bottom": round(z.bottom, digits)}
            for z in structure.breaker_blocks[:2]
        ],
        "fair_value_gaps": [
            {"direction": z.direction, "top": round(z.top, digits),
             "bottom": round(z.bottom, digits), "filled": z.mitigated,
             "distance_pct": z.distance_pct}
            for z in structure.fair_value_gaps[:3]
        ],
        "liquidity_pools": [
            {"direction": z.direction, "level": round(z.mid, digits), "note": z.note}
            for z in structure.liquidity_pools[:4]
        ],
        "equal_highs": [round(v, digits) for v in structure.equal_highs[:3]],
        "equal_lows": [round(v, digits) for v in structure.equal_lows[:3]],
        "range": {
            "high": structure.range_high, "low": structure.range_low,
            "equilibrium": structure.equilibrium, "position": structure.premium_discount,
        },
        "support": [round(v, digits) for v in structure.support_levels[:4]],
        "resistance": [round(v, digits) for v in structure.resistance_levels[:4]],
        "notes": structure.notes,
    }
