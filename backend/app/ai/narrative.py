"""Evidence brief construction.

Turns the analysis engine's output into the two things Claude reads: a
readable market narrative, and a compact structured evidence object. The
narrative exists because plain language carries relationships that a nested
dict does not — "price is approaching the resistance the trader drew, on
rising volume, with RSI at 69" is a different claim than three separate
numbers.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..data.sessions import market_clock
from ..instruments import Instrument
from ..schemas.analysis import (
    ConfidenceBreakdown, IndicatorSnapshot, MarketStructure, MTFReport, PatternHit,
)
from ..schemas.drawings import DrawingAnalytics
from ..schemas.market import QuoteSnapshot
from ..analysis import drawings as drawing_analysis
from ..analysis import mtf as mtf_analysis
from ..analysis import structure as structure_analysis


def _fmt(value: Optional[float], digits: int) -> str:
    return "n/a" if value is None else "{:.{d}f}".format(value, d=digits)


# ── narrative ─────────────────────────────────────────────────

def _indicator_lines(snap: IndicatorSnapshot, inst: Instrument, price: float) -> List[str]:
    d = inst.digits
    lines: List[str] = []

    ema20, ema50, ema200 = snap.ema.get("20"), snap.ema.get("50"), snap.ema.get("200")
    if ema20 and ema50:
        stack = "above" if price > ema20 else "below"
        order = ("20>50" if ema20 > ema50 else "50>20")
        extra = ""
        if ema200:
            extra = ", 200 EMA at {}".format(_fmt(ema200, d))
        lines.append("Price is {} the 20 EMA ({}), EMAs stacked {}{}.".format(
            stack, _fmt(ema20, d), order, extra))

    if snap.rsi is not None:
        state = ("overbought" if snap.rsi > 70 else
                 "oversold" if snap.rsi < 30 else "neutral")
        line = "RSI {:.0f} ({})".format(snap.rsi, state)
        if snap.rsi_divergence:
            line += " with {} divergence".format(snap.rsi_divergence.replace("_", " "))
        lines.append(line + ".")

    if snap.macd:
        lines.append("MACD histogram {:.5f}, {}.".format(
            snap.macd.get("hist", 0.0), (snap.macd_state or "flat").replace("_", " ")))

    if snap.adx is not None:
        lines.append("ADX {:.0f} ({} trend), +DI {:.0f} / -DI {:.0f}.".format(
            snap.adx, snap.trend_strength, snap.di_plus or 0, snap.di_minus or 0))

    if snap.atr_pips:
        lines.append("ATR {:.1f} pips ({:.2f}% of price); volatility regime is {}.".format(
            snap.atr_pips, snap.atr_pct or 0.0, snap.volatility_regime))

    if snap.bb_squeeze:
        lines.append("Bollinger bands are compressed — expansion is likely, direction unresolved.")

    if snap.relative_volume is not None:
        descriptor = ("well above average" if snap.relative_volume > 1.5 else
                      "above average" if snap.relative_volume > 1.1 else
                      "thin" if snap.relative_volume < 0.7 else "average")
        lines.append("Current bar volume is {:.1f}x the 20-bar average ({}).".format(
            snap.relative_volume, descriptor))

    if snap.obv_slope is not None:
        lines.append("OBV is {} over the last 20 bars.".format(
            "rising" if snap.obv_slope > 0.05 else
            "falling" if snap.obv_slope < -0.05 else "flat"))

    profile = snap.volume_profile or {}
    if profile.get("poc"):
        rel = "above" if price > profile["poc"] else "below"
        lines.append("Volume profile POC {} — price is trading {} it; value area {} to {}.".format(
            _fmt(profile["poc"], d), rel,
            _fmt(profile.get("val"), d), _fmt(profile.get("vah"), d)))

    if snap.ichimoku_state:
        cloud = {"above_cloud": "trading above the Ichimoku cloud",
                 "below_cloud": "trading below the Ichimoku cloud",
                 "in_cloud": "inside the Ichimoku cloud — no trend confirmation"}
        lines.append("Price is {}.".format(
            cloud.get(snap.ichimoku_state, snap.ichimoku_state)))

    if snap.supertrend_dir and snap.supertrend is not None:
        lines.append("SuperTrend is {} with the line at {}.".format(
            "long" if snap.supertrend_dir > 0 else "short", _fmt(snap.supertrend, d)))

    return lines


def _structure_lines(struct: MarketStructure, inst: Instrument) -> List[str]:
    d = inst.digits
    lines = ["Market structure reads {} ({} bias).".format(
        struct.structure_label, struct.trend.value)]

    if struct.last_bos:
        lines.append("Last break of structure was {} at {}, {} bars ago.".format(
            struct.last_bos.direction, _fmt(struct.last_bos.price, d),
            struct.last_bos.bars_ago))
    if struct.last_choch:
        lines.append("Last change of character was {} at {}, {} bars ago.".format(
            struct.last_choch.direction, _fmt(struct.last_choch.price, d),
            struct.last_choch.bars_ago))

    sweeps = [e for e in struct.events if e.kind == "liquidity_sweep" and e.bars_ago <= 12]
    if sweeps:
        latest = sweeps[-1]
        lines.append(
            "A {} liquidity sweep printed {} bars ago at {} — stops were taken and price "
            "closed back inside.".format(latest.direction, latest.bars_ago,
                                         _fmt(latest.price, d)))

    if struct.range_high and struct.range_low:
        lines.append("Working range {} to {} with equilibrium at {}; price is in {}.".format(
            _fmt(struct.range_low, d), _fmt(struct.range_high, d),
            _fmt(struct.equilibrium, d), struct.premium_discount))

    for zone in struct.order_blocks[:2]:
        lines.append("{} order block {} to {} ({:.2f}% away, tested {}x).".format(
            zone.direction.title(), _fmt(zone.bottom, d), _fmt(zone.top, d),
            zone.distance_pct, zone.tested))
    for zone in struct.fair_value_gaps[:2]:
        lines.append("Unfilled {} fair value gap {} to {} ({:.2f}% away).".format(
            zone.direction, _fmt(zone.bottom, d), _fmt(zone.top, d), zone.distance_pct))
    for zone in struct.liquidity_pools[:2]:
        lines.append("Liquidity resting near {} — {}.".format(_fmt(zone.mid, d), zone.note))

    if struct.equal_highs:
        lines.append("Equal highs at {} (buy-side liquidity).".format(
            ", ".join(_fmt(v, d) for v in struct.equal_highs[:2])))
    if struct.equal_lows:
        lines.append("Equal lows at {} (sell-side liquidity).".format(
            ", ".join(_fmt(v, d) for v in struct.equal_lows[:2])))

    if struct.support_levels:
        lines.append("Nearest support: {}.".format(
            ", ".join(_fmt(v, d) for v in struct.support_levels[:3])))
    if struct.resistance_levels:
        lines.append("Nearest resistance: {}.".format(
            ", ".join(_fmt(v, d) for v in struct.resistance_levels[:3])))

    return lines


def _drawing_lines(analytics: List[DrawingAnalytics], inst: Instrument) -> List[str]:
    """The trader's own work, described the way they would describe it."""
    if not analytics:
        return ["The trader has not drawn anything on this chart yet."]

    d = inst.digits
    lines: List[str] = []
    for item in analytics:
        if item.role == "annotation":
            if item.notes:
                lines.append(item.notes[0])
            continue

        label = item.type.value.replace("_", " ")
        if item.upper_value is not None and item.lower_value is not None:
            where = "{} to {}".format(_fmt(item.lower_value, d), _fmt(item.upper_value, d))
        else:
            where = "currently projecting {}".format(_fmt(item.current_value, d))

        sentence = "The trader drew a {} ({}) {}, spanning {} bars.".format(
            label, item.role, where, item.length_bars)

        if item.touches or item.breaks:
            sentence += (" Price tested it {} time(s) with {} clean reaction(s)"
                         .format(item.touches, item.reactions))
            if item.fake_breaks:
                sentence += ", {} failed break(s)".format(item.fake_breaks)
            if item.breaks:
                sentence += ", and closed through it {} time(s)".format(item.breaks)
            sentence += "."
        else:
            sentence += " It has not been tested yet."

        sentence += " Reliability score {:.0f}/100, currently {}.".format(
            item.strength, item.validity)

        if item.approaching:
            sentence += (" Price is approaching it from {} — {:.1f} pips ({:.2f} ATR) away."
                         .format(item.price_side, item.distance_pips, item.distance_atr))
        elif item.price_side == "inside":
            sentence += " Price is inside this zone right now."
        else:
            sentence += " Price is {} it, {:.1f} pips away.".format(
                item.price_side, item.distance_pips)

        if item.volume_at_touches > 1.25:
            sentence += " Interactions came on {:.1f}x average volume.".format(
                item.volume_at_touches)

        if item.angle_degrees:
            sentence += " The line slopes {:.0f}° in ATR-normalised terms.".format(
                item.angle_degrees)

        if item.fib_levels:
            golden = item.fib_levels.get("0.618") or item.fib_levels.get("1.618")
            if golden is not None:
                sentence += " Golden ratio sits at {}.".format(_fmt(golden, d))

        lines.append(sentence)

    return lines


def _pattern_lines(chart: List[PatternHit], candles: List[PatternHit]) -> List[str]:
    lines: List[str] = []
    for hit in chart[:3]:
        line = "{} detected ({} bias, {:.0f}% fit)".format(
            hit.name, hit.direction, hit.confidence * 100)
        if hit.target:
            line += ", measured target {:.5f}".format(hit.target)
        if hit.invalidation:
            line += ", invalidated below/above {:.5f}".format(hit.invalidation)
        lines.append(line + ".")
    if candles:
        described = ["{} ({}, {})".format(h.name, h.direction, h.note or "recent")
                     for h in candles[:4]]
        lines.append("Recent candlestick signals: {}.".format("; ".join(described)))
    if not lines:
        lines.append("No significant chart or candlestick patterns are currently formed.")
    return lines


def build_narrative(symbol: str, timeframe: str, inst: Instrument, price: float,
                    quote: Optional[QuoteSnapshot], snap: IndicatorSnapshot,
                    struct: MarketStructure, mtf: MTFReport,
                    chart_patterns: List[PatternHit], candle_patterns: List[PatternHit],
                    drawing_stats: List[DrawingAnalytics],
                    confidence: ConfidenceBreakdown, bars: int) -> str:
    d = inst.digits
    clock = market_clock()

    header = [
        "{} ({}) on the {} timeframe.".format(inst.display, symbol, timeframe),
        "Last price {}{}.".format(
            _fmt(price, d),
            ", spread {:.1f} pips".format(quote.spread_pips) if quote else ""),
    ]
    if quote and quote.change_pct:
        header.append("Session change {:+.2f}% ({:+.5f}).".format(
            quote.change_pct, quote.change_abs))
    header.append("Session: {} ({} liquidity). {} bars of history analysed.".format(
        clock.active_session, clock.liquidity_regime, bars))

    sections = [
        ("SNAPSHOT", header),
        ("MULTI-TIMEFRAME", [
            mtf.narrative,
            "Fused directional score {:+.2f} with {:.0f}% alignment; combined bias is {}.".format(
                mtf.weighted_score, mtf.alignment_score * 100, mtf.aligned_bias.value),
        ] + (["Conflicts: " + "; ".join(mtf.conflicts) + "."] if mtf.conflicts else [])),
        ("INDICATORS", _indicator_lines(snap, inst, price)),
        ("MARKET STRUCTURE", _structure_lines(struct, inst)),
        ("TRADER'S DRAWINGS", _drawing_lines(drawing_stats, inst)),
        ("PATTERNS", _pattern_lines(chart_patterns, candle_patterns)),
        ("QUANTITATIVE FUSION", [
            "{} — {}".format(c.name.title(), c.detail or "no signal")
            for c in confidence.components if c.name != "ai"
        ] + [
            "Weighted directional score {:+.2f}, component agreement {:.0f}%, "
            "pre-AI confidence {:.0f}/100.".format(
                confidence.directional_score, confidence.agreement * 100,
                confidence.final_confidence),
            confidence.reason,
        ]),
    ]

    out: List[str] = []
    for title, lines in sections:
        body = [l for l in lines if l]
        if not body:
            continue
        out.append("## {}".format(title))
        out.extend("- {}".format(line) for line in body)
        out.append("")
    return "\n".join(out).strip()


# ── structured evidence ───────────────────────────────────────

def build_evidence(symbol: str, timeframe: str, inst: Instrument, price: float,
                   quote: Optional[QuoteSnapshot], snap: IndicatorSnapshot,
                   struct: MarketStructure, mtf: MTFReport,
                   chart_patterns: List[PatternHit], candle_patterns: List[PatternHit],
                   drawing_stats: List[DrawingAnalytics],
                   confidence: ConfidenceBreakdown) -> Dict[str, Any]:
    d = inst.digits
    clock = market_clock()

    return {
        "instrument": {
            "symbol": symbol,
            "display": inst.display,
            "asset_class": inst.asset_class,
            "timeframe": timeframe,
            "digits": d,
            "pip_size": inst.pip_size,
            "price": round(price, d),
            "spread_pips": quote.spread_pips if quote else inst.typical_spread_pips,
            "session": clock.active_session,
            "liquidity_regime": clock.liquidity_regime,
            "utc_time": clock.utc_time,
        },
        "indicators": {
            "ema": snap.ema,
            "vwap": snap.vwap,
            "rsi": snap.rsi,
            "rsi_divergence": snap.rsi_divergence,
            "macd": snap.macd,
            "macd_state": snap.macd_state,
            "adx": snap.adx,
            "di_plus": snap.di_plus,
            "di_minus": snap.di_minus,
            "stoch_rsi": snap.stoch_rsi,
            "atr": snap.atr,
            "atr_pips": snap.atr_pips,
            "bollinger": snap.bollinger,
            "bb_squeeze": snap.bb_squeeze,
            "ichimoku_state": snap.ichimoku_state,
            "supertrend_direction": snap.supertrend_dir,
            "obv_slope": snap.obv_slope,
            "mfi": snap.mfi,
            "cci": snap.cci,
            "williams_r": snap.williams_r,
            "relative_volume": snap.relative_volume,
            "volatility_regime": snap.volatility_regime,
            "trend_strength": snap.trend_strength,
            "pivots": snap.pivots,
            "volume_profile": {
                k: v for k, v in (snap.volume_profile or {}).items() if k != "bins"
            },
        },
        "market_structure": structure_analysis.summarise(struct, d),
        "multi_timeframe": mtf_analysis.summarise(mtf),
        "patterns": {
            "chart": [
                {"name": p.name, "direction": p.direction,
                 "confidence": p.confidence, "target": p.target,
                 "invalidation": p.invalidation, "note": p.note}
                for p in chart_patterns[:4]
            ],
            "candlestick": [
                {"name": p.name, "direction": p.direction,
                 "confidence": p.confidence, "when": p.note}
                for p in candle_patterns[:6]
            ],
        },
        "user_drawings": drawing_analysis.summarise(drawing_stats, d),
        "quantitative_fusion": {
            "components": [
                {"name": c.name, "weight": c.weight, "score": c.raw_score,
                 "contribution": c.contribution, "detail": c.detail}
                for c in confidence.components if c.name != "ai"
            ],
            "directional_score": confidence.directional_score,
            "agreement": confidence.agreement,
            "pre_ai_confidence": confidence.final_confidence,
            "conflict_detected": confidence.conflict,
            "reason": confidence.reason,
        },
    }
