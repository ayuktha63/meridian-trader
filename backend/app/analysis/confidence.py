"""Confidence fusion.

Six independently-computed components, each producing a directional score in
[-1, +1], combined with fixed weights:

    trend 25% · momentum 20% · volume 15% · structure 20% · pattern 10% · AI 10%

The headline number is *not* the weighted average — it is the weighted
average scaled by how much the components agree. Six weak signals pointing
the same way beat two strong ones fighting each other, and a genuinely split
book collapses the score toward zero, which is what forces `WAIT`.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..schemas.analysis import (
    AIVerdict, Bias, ConfidenceBreakdown, ConfidenceComponent, Decision,
    IndicatorSnapshot, MarketStructure, MTFReport, PatternHit,
)
from ..schemas.drawings import DrawingAnalytics

WEIGHTS: Dict[str, float] = {
    "trend": 0.25,
    "momentum": 0.20,
    "volume": 0.15,
    "structure": 0.20,
    "pattern": 0.10,
    "ai": 0.10,
}

DECISION_THRESHOLD = 0.20      # |directional score| needed to act
MIN_AGREEMENT = 0.58           # below this the book is split → WAIT
MIN_CONFIDENCE = 45.0          # below this the edge is not worth the spread

#: The directional score is a weighted mean of component scores, and each
#: component is itself a mean of sub-signals that partially cancel. Empirically
#: it saturates near ±0.60 even in strongly trending, fully-aligned markets —
#: never ±1.0. Treating 0.60 as full conviction keeps the 0-100 confidence
#: scale usable; mapping the raw score linearly instead would compress every
#: real setup into the bottom third and the platform would only ever say WAIT.
FULL_CONVICTION_SCORE = 0.60


def score_to_confidence(directional: float, agreement: float) -> float:
    """Map (directional score, component agreement) onto 0-100.

    Agreement is a multiplier, not an addend: aligned evidence keeps the full
    score, a split book is discounted toward zero.
    """
    raw = abs(directional) / FULL_CONVICTION_SCORE * 100.0
    return round(max(0.0, min(100.0, raw * (0.55 + 0.45 * agreement))), 1)


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


# ── components ────────────────────────────────────────────────

def _trend_component(snap: IndicatorSnapshot, mtf: MTFReport,
                     structure: MarketStructure) -> ConfidenceComponent:
    parts: List[float] = []
    notes: List[str] = []

    parts.append(_clamp(mtf.weighted_score * 1.15))
    notes.append("MTF {:+.2f} ({:.0f}% aligned)".format(
        mtf.weighted_score, mtf.alignment_score * 100))

    ema = snap.ema
    if ema.get("20") and ema.get("50"):
        stack = 0.0
        stack += 0.5 if ema["20"] > ema["50"] else -0.5
        if ema.get("200"):
            stack += 0.5 if ema["50"] > ema["200"] else -0.5
        parts.append(stack)
        notes.append("EMA stack {}".format("bullish" if stack > 0 else "bearish"))

    if snap.adx is not None and snap.di_plus is not None and snap.di_minus is not None:
        total = snap.di_plus + snap.di_minus
        di = ((snap.di_plus - snap.di_minus) / total) if total else 0.0
        parts.append(_clamp(di * min(snap.adx / 28.0, 1.0)))
        notes.append("ADX {:.0f} ({})".format(snap.adx, snap.trend_strength))

    if snap.supertrend_dir:
        parts.append(0.7 * snap.supertrend_dir)
        notes.append("SuperTrend {}".format("long" if snap.supertrend_dir > 0 else "short"))

    if snap.ichimoku_state == "above_cloud":
        parts.append(0.6)
        notes.append("above Kumo")
    elif snap.ichimoku_state == "below_cloud":
        parts.append(-0.6)
        notes.append("below Kumo")
    elif snap.ichimoku_state == "in_cloud":
        parts.append(0.0)
        notes.append("inside Kumo (no trend)")

    raw = _clamp(sum(parts) / max(len(parts), 1))
    return ConfidenceComponent(
        name="trend", weight=WEIGHTS["trend"], raw_score=round(raw, 3),
        contribution=round(raw * WEIGHTS["trend"] * 100, 2),
        detail=" · ".join(notes),
    )


def _momentum_component(snap: IndicatorSnapshot) -> ConfidenceComponent:
    parts: List[float] = []
    notes: List[str] = []

    if snap.rsi is not None:
        parts.append(_clamp((snap.rsi - 50.0) / 20.0))
        state = ("overbought" if snap.rsi > 70 else "oversold" if snap.rsi < 30 else "neutral")
        notes.append("RSI {:.0f} ({})".format(snap.rsi, state))
        # Stretched momentum argues against chasing, it does not flip direction.
        if snap.rsi > 78:
            parts.append(-0.35)
        elif snap.rsi < 22:
            parts.append(0.35)

    if snap.macd:
        hist = snap.macd.get("hist", 0.0)
        line = snap.macd.get("line", 0.0)
        scale = max(abs(line), abs(hist), 1e-9)
        parts.append(_clamp(hist / scale))
        notes.append("MACD {}".format((snap.macd_state or "").replace("_", " ")))

    if snap.stoch_rsi:
        k = snap.stoch_rsi.get("k", 50.0)
        d = snap.stoch_rsi.get("d", 50.0)
        parts.append(_clamp((k - 50.0) / 45.0) * 0.7)
        if k > d and k < 30:
            parts.append(0.5)
            notes.append("StochRSI turning up from oversold")
        elif k < d and k > 70:
            parts.append(-0.5)
            notes.append("StochRSI rolling over from overbought")
        else:
            notes.append("StochRSI {:.0f}/{:.0f}".format(k, d))

    if snap.roc is not None:
        parts.append(_clamp(snap.roc / 1.5) * 0.6)

    if snap.rsi_divergence:
        if snap.rsi_divergence.startswith("bullish"):
            parts.append(0.55)
        else:
            parts.append(-0.55)
        notes.append("{} divergence".format(snap.rsi_divergence.replace("_", " ")))

    raw = _clamp(sum(parts) / max(len(parts), 1))
    return ConfidenceComponent(
        name="momentum", weight=WEIGHTS["momentum"], raw_score=round(raw, 3),
        contribution=round(raw * WEIGHTS["momentum"] * 100, 2),
        detail=" · ".join(notes),
    )


def _volume_component(snap: IndicatorSnapshot, price: float) -> ConfidenceComponent:
    parts: List[float] = []
    notes: List[str] = []

    if snap.obv_slope is not None:
        parts.append(_clamp(snap.obv_slope * 1.4))
        notes.append("OBV {}".format("rising" if snap.obv_slope > 0 else "falling"))

    if snap.mfi is not None:
        parts.append(_clamp((snap.mfi - 50.0) / 25.0) * 0.8)
        notes.append("MFI {:.0f}".format(snap.mfi))

    profile = snap.volume_profile or {}
    poc = profile.get("poc")
    val, vah = profile.get("val"), profile.get("vah")
    if poc:
        parts.append(0.45 if price > poc else -0.45)
        notes.append("price {} POC".format("above" if price > poc else "below"))
    if val is not None and vah is not None:
        if price > vah:
            parts.append(0.3)
            notes.append("above value area")
        elif price < val:
            parts.append(-0.3)
            notes.append("below value area")

    rvol = snap.relative_volume
    if rvol is not None:
        notes.append("RVOL {:.1f}x".format(rvol))
        # Volume confirms whatever the other components already say; on its own
        # it is directionless, so it scales rather than adds.
        if rvol < 0.6:
            parts = [p * 0.6 for p in parts]
            notes.append("thin participation")

    raw = _clamp(sum(parts) / max(len(parts), 1))
    return ConfidenceComponent(
        name="volume", weight=WEIGHTS["volume"], raw_score=round(raw, 3),
        contribution=round(raw * WEIGHTS["volume"] * 100, 2),
        detail=" · ".join(notes),
    )


def _structure_component(structure: MarketStructure, price: float,
                         drawings: Optional[List[DrawingAnalytics]] = None,
                         atr: float = 0.0) -> ConfidenceComponent:
    parts: List[float] = []
    notes: List[str] = []

    if structure.trend == Bias.BULLISH:
        parts.append(0.7)
    elif structure.trend == Bias.BEARISH:
        parts.append(-0.7)
    notes.append(structure.structure_label)

    if structure.last_bos and structure.last_bos.bars_ago <= 20:
        sign = 1.0 if structure.last_bos.direction == "bullish" else -1.0
        parts.append(sign * (0.8 - structure.last_bos.bars_ago * 0.02))
        notes.append("{} BOS {}b ago".format(structure.last_bos.direction,
                                             structure.last_bos.bars_ago))
    if structure.last_choch and structure.last_choch.bars_ago <= 10:
        sign = 1.0 if structure.last_choch.direction == "bullish" else -1.0
        parts.append(sign * 0.6)
        notes.append("{} CHoCH {}b ago".format(structure.last_choch.direction,
                                               structure.last_choch.bars_ago))

    sweeps = [e for e in structure.events if e.kind == "liquidity_sweep" and e.bars_ago <= 6]
    if sweeps:
        latest = sweeps[-1]
        parts.append(0.5 if latest.direction == "bullish" else -0.5)
        notes.append("{} sweep {}b ago".format(latest.direction, latest.bars_ago))

    if structure.premium_discount == "discount":
        parts.append(0.35)
        notes.append("discount pricing")
    elif structure.premium_discount == "premium":
        parts.append(-0.35)
        notes.append("premium pricing")

    # Nearby unmitigated zones tilt the odds toward their direction.
    for zone in (structure.order_blocks[:2] + structure.fair_value_gaps[:2]
                 + structure.demand_zones[:1] + structure.supply_zones[:1]):
        if zone.distance_pct > 0.35:
            continue
        sign = 1.0 if zone.direction == "bullish" else -1.0
        parts.append(sign * 0.4 * zone.strength)
        notes.append("{} {} nearby".format(zone.direction, zone.kind.replace("_", " ")))

    # User drawings are structure too — weighted by how well they have held.
    for drawing in (drawings or []):
        if drawing.role not in ("support", "resistance", "supply", "demand", "zone"):
            continue
        if drawing.validity == "invalidated" or drawing.strength < 25:
            continue
        if drawing.distance_atr > 1.5:
            continue
        weight = min(drawing.strength / 100.0, 1.0) * 0.75
        if drawing.role in ("support", "demand"):
            sign = 1.0 if drawing.price_side in ("above", "inside") else -0.4
        else:
            sign = -1.0 if drawing.price_side in ("below", "inside") else 0.4
        if drawing.validity == "broken":
            sign *= -0.6      # a broken level flips
        parts.append(sign * weight)
        notes.append("user {} ({} touches, {})".format(
            drawing.role, drawing.touches, drawing.validity))

    raw = _clamp(sum(parts) / max(len(parts), 1))
    return ConfidenceComponent(
        name="structure", weight=WEIGHTS["structure"], raw_score=round(raw, 3),
        contribution=round(raw * WEIGHTS["structure"] * 100, 2),
        detail=" · ".join(notes[:6]),
    )


def _pattern_component(chart: List[PatternHit],
                       candles: List[PatternHit]) -> ConfidenceComponent:
    parts: List[float] = []
    notes: List[str] = []

    for hit in chart[:3]:
        if hit.direction == "neutral":
            notes.append("{} (neutral)".format(hit.name))
            continue
        sign = 1.0 if hit.direction == "bullish" else -1.0
        parts.append(sign * hit.confidence)
        notes.append("{} {:.0f}%".format(hit.name, hit.confidence * 100))

    for hit in candles[:4]:
        if hit.direction == "neutral":
            continue
        sign = 1.0 if hit.direction == "bullish" else -1.0
        parts.append(sign * hit.confidence * 0.7)
        notes.append(hit.name)

    raw = _clamp(sum(parts) / max(len(parts), 1)) if parts else 0.0
    return ConfidenceComponent(
        name="pattern", weight=WEIGHTS["pattern"], raw_score=round(raw, 3),
        contribution=round(raw * WEIGHTS["pattern"] * 100, 2),
        detail=" · ".join(notes[:5]) or "no significant patterns",
    )


def _ai_component(verdict: Optional[AIVerdict]) -> ConfidenceComponent:
    raw = 0.0
    detail = "not yet consulted"
    if verdict is not None:
        strength = _clamp(verdict.confidence / 100.0, 0.0, 1.0)
        if verdict.decision == Decision.BUY:
            raw = strength
        elif verdict.decision == Decision.SELL:
            raw = -strength
        detail = "{} @ {:.0f}% ({})".format(
            verdict.decision.value, verdict.confidence, verdict.source)
    return ConfidenceComponent(
        name="ai", weight=WEIGHTS["ai"], raw_score=round(raw, 3),
        contribution=round(raw * WEIGHTS["ai"] * 100, 2),
        detail=detail,
    )


# ── fusion ────────────────────────────────────────────────────

def compute(snap: IndicatorSnapshot, structure: MarketStructure, mtf: MTFReport,
            chart_patterns: List[PatternHit], candle_patterns: List[PatternHit],
            price: float, drawings: Optional[List[DrawingAnalytics]] = None,
            verdict: Optional[AIVerdict] = None,
            atr: float = 0.0) -> ConfidenceBreakdown:
    components = [
        _trend_component(snap, mtf, structure),
        _momentum_component(snap),
        _volume_component(snap, price),
        _structure_component(structure, price, drawings, atr),
        _pattern_component(chart_patterns, candle_patterns),
        _ai_component(verdict),
    ]

    # Normalise by the weight that actually participated. Without this, a
    # pre-AI score is capped at 0.90 while a post-AI score can reach 1.00, and
    # the two are not comparable — the same market would appear to gain
    # conviction purely because Claude was consulted.
    participating = [c for c in components if c.name != "ai" or verdict is not None]
    active_weight = sum(c.weight for c in participating) or 1.0
    directional = sum(c.raw_score * c.weight for c in participating) / active_weight

    meaningful = [c for c in components if abs(c.raw_score) > 0.12]
    if meaningful and abs(directional) > 1e-9:
        dominant = 1 if directional > 0 else -1
        agreeing = sum(c.weight for c in meaningful
                       if (1 if c.raw_score > 0 else -1) == dominant)
        agreement = agreeing / sum(c.weight for c in meaningful)
    else:
        agreement = 0.0

    confidence = score_to_confidence(directional, agreement)

    conflict = agreement < MIN_AGREEMENT or abs(directional) < DECISION_THRESHOLD

    if conflict and abs(directional) < DECISION_THRESHOLD:
        reason = "No directional edge — components net to {:+.2f}".format(directional)
    elif conflict:
        opposed = [c.name for c in meaningful
                   if (1 if c.raw_score > 0 else -1) != (1 if directional > 0 else -1)]
        reason = "Components disagree ({:.0f}% aligned); {} argue the other way".format(
            agreement * 100, ", ".join(opposed) or "several")
    else:
        reason = "{:.0f}% of weighted evidence agrees on a {} bias".format(
            agreement * 100, "bullish" if directional > 0 else "bearish")

    return ConfidenceBreakdown(
        components=components,
        directional_score=round(directional, 3),
        agreement=round(agreement, 3),
        final_confidence=confidence,
        conflict=conflict,
        reason=reason,
    )


def decide(breakdown: ConfidenceBreakdown, mtf: Optional[MTFReport] = None) -> Decision:
    """Translate the fused score into an action. Ties always resolve to WAIT."""
    if breakdown.conflict:
        return Decision.WAIT
    if breakdown.final_confidence < MIN_CONFIDENCE:
        return Decision.WAIT
    if breakdown.directional_score >= DECISION_THRESHOLD:
        return Decision.BUY
    if breakdown.directional_score <= -DECISION_THRESHOLD:
        return Decision.SELL
    return Decision.WAIT


def probability(breakdown: ConfidenceBreakdown, risk_reward: Optional[float] = None) -> float:
    """A heuristic score on a 30–72 scale. Not a probability.

    It is the confidence score squeezed into a narrower range and nudged by
    the reward-to-risk ratio. Nothing has ever fitted it to outcomes, and
    where it was checked it carried none: over 778 replayed setups about
    three in ten won whether the score was under 50 or over 70 (Brier 0.343
    against 0.210 for always guessing the base rate —
    docs/research/BASELINE.md §7). The field is still called `probability`
    for the stored records' sake; the interface must not call it one.
    """
    base = 38.0 + breakdown.final_confidence * 0.34
    if risk_reward and risk_reward > 0:
        # Wide targets are hit less often; tight ones more.
        base -= max(0.0, (risk_reward - 2.0)) * 3.0
        base += max(0.0, (1.5 - risk_reward)) * 4.0
    return round(max(30.0, min(72.0, base)), 1)
