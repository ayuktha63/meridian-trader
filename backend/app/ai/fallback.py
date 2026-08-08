"""Deterministic analyst.

Runs whenever Claude is unavailable, declines, or errors. It emits the exact
same `AIVerdict` contract, built from the quantitative fusion the engine has
already computed — so the platform degrades in tone, not in structure. The UI,
journal and backtester never need to know which analyst produced a verdict.
"""
from __future__ import annotations

from typing import List, Optional

from ..instruments import Instrument
from ..schemas.analysis import (
    AIVerdict, Bias, ConfidenceBreakdown, Decision, IndicatorSnapshot,
    MarketStructure, MTFReport, PatternHit, TradePlan,
)
from ..schemas.drawings import DrawingAnalytics
from ..analysis.confidence import probability


def _evidence_points(snap: IndicatorSnapshot, struct: MarketStructure, mtf: MTFReport,
                     chart: List[PatternHit], candles: List[PatternHit],
                     drawings: List[DrawingAnalytics], digits: int):
    bull: List[str] = []
    bear: List[str] = []

    def add(condition: bool, bullish: str, bearish: str) -> None:
        (bull if condition else bear).append(bullish if condition else bearish)

    if mtf.aligned_bias == Bias.BULLISH:
        bull.append("Multi-timeframe bias is bullish ({:.0f}% aligned): {}".format(
            mtf.alignment_score * 100, mtf.narrative))
    elif mtf.aligned_bias == Bias.BEARISH:
        bear.append("Multi-timeframe bias is bearish ({:.0f}% aligned): {}".format(
            mtf.alignment_score * 100, mtf.narrative))
    for conflict in mtf.conflicts[:2]:
        (bear if mtf.aligned_bias == Bias.BULLISH else bull).append(
            "Timeframe conflict: {}".format(conflict))

    if struct.trend == Bias.BULLISH:
        bull.append("Structure is {} with higher highs and higher lows".format(
            struct.structure_label))
    elif struct.trend == Bias.BEARISH:
        bear.append("Structure is {} with lower highs and lower lows".format(
            struct.structure_label))

    if struct.last_bos and struct.last_bos.bars_ago <= 15:
        add(struct.last_bos.direction == "bullish",
            "Bullish break of structure {} bars ago at {:.{d}f}".format(
                struct.last_bos.bars_ago, struct.last_bos.price, d=digits),
            "Bearish break of structure {} bars ago at {:.{d}f}".format(
                struct.last_bos.bars_ago, struct.last_bos.price, d=digits))
    if struct.last_choch and struct.last_choch.bars_ago <= 10:
        add(struct.last_choch.direction == "bullish",
            "Fresh bullish change of character — sellers lost control",
            "Fresh bearish change of character — buyers lost control")

    sweeps = [e for e in struct.events if e.kind == "liquidity_sweep" and e.bars_ago <= 8]
    if sweeps:
        latest = sweeps[-1]
        add(latest.direction == "bullish",
            "Sell-side liquidity swept {} bars ago, price reclaimed".format(latest.bars_ago),
            "Buy-side liquidity swept {} bars ago, price rejected".format(latest.bars_ago))

    if struct.premium_discount == "discount":
        bull.append("Price is in discount relative to the working range")
    elif struct.premium_discount == "premium":
        bear.append("Price is in premium relative to the working range — longs are late")

    if snap.rsi is not None:
        if snap.rsi > 70:
            bear.append("RSI {:.0f} is overbought".format(snap.rsi))
        elif snap.rsi < 30:
            bull.append("RSI {:.0f} is oversold".format(snap.rsi))
        elif snap.rsi > 55:
            bull.append("RSI {:.0f} holding above the midline".format(snap.rsi))
        elif snap.rsi < 45:
            bear.append("RSI {:.0f} holding below the midline".format(snap.rsi))
    if snap.rsi_divergence:
        add(snap.rsi_divergence.startswith("bullish"),
            "{} on RSI".format(snap.rsi_divergence.replace("_", " ").title()),
            "{} on RSI".format(snap.rsi_divergence.replace("_", " ").title()))

    if snap.macd_state:
        add(snap.macd_state.startswith("bullish"),
            "MACD {}".format(snap.macd_state.replace("_", " ")),
            "MACD {}".format(snap.macd_state.replace("_", " ")))

    if snap.adx is not None and snap.adx >= 25 and snap.di_plus is not None:
        add(snap.di_plus > (snap.di_minus or 0),
            "ADX {:.0f} with +DI leading — trend has conviction".format(snap.adx),
            "ADX {:.0f} with -DI leading — trend has conviction".format(snap.adx))
    elif snap.adx is not None and snap.adx < 18:
        bear.append("ADX {:.0f} — no directional conviction, range conditions".format(snap.adx))
        bull.append("ADX {:.0f} — no directional conviction, range conditions".format(snap.adx))

    if snap.relative_volume is not None and snap.relative_volume < 0.7:
        note = "Participation is thin ({:.1f}x average volume)".format(snap.relative_volume)
        bull.append(note)
        bear.append(note)
    elif snap.relative_volume is not None and snap.relative_volume > 1.4:
        add((snap.obv_slope or 0) > 0,
            "Volume {:.1f}x average with OBV rising".format(snap.relative_volume),
            "Volume {:.1f}x average with OBV falling".format(snap.relative_volume))

    if snap.bb_squeeze:
        note = "Bollinger squeeze — expansion pending, direction unresolved"
        bull.append(note)
        bear.append(note)

    for hit in chart[:2]:
        if hit.direction == "bullish":
            bull.append("{} ({:.0f}% fit)".format(hit.name, hit.confidence * 100))
        elif hit.direction == "bearish":
            bear.append("{} ({:.0f}% fit)".format(hit.name, hit.confidence * 100))
    for hit in candles[:3]:
        if hit.direction == "bullish":
            bull.append("{} candlestick {}".format(hit.name, hit.note or ""))
        elif hit.direction == "bearish":
            bear.append("{} candlestick {}".format(hit.name, hit.note or ""))

    for item in drawings:
        if item.role not in ("support", "resistance", "supply", "demand", "zone"):
            continue
        descriptor = "Your {} at {:.{d}f} ({} touches, {})".format(
            item.role, item.current_value or 0.0, item.touches, item.validity, d=digits)
        if item.validity == "broken":
            (bull if item.role in ("resistance", "supply") else bear).append(
                descriptor + " has been broken — expect it to flip")
        elif item.role in ("support", "demand"):
            bull.append(descriptor + " is holding beneath price")
        else:
            bear.append(descriptor + " is capping price")

    return bull[:8], bear[:8]


def build(decision: Decision, breakdown: ConfidenceBreakdown, plan: TradePlan,
          snap: IndicatorSnapshot, struct: MarketStructure, mtf: MTFReport,
          chart: List[PatternHit], candles: List[PatternHit],
          drawings: List[DrawingAnalytics], instrument: Instrument,
          reason: Optional[str] = None) -> AIVerdict:
    digits = instrument.digits
    bull, bear = _evidence_points(snap, struct, mtf, chart, candles, drawings, digits)

    if decision == Decision.WAIT:
        summary = (
            "No actionable setup right now. {} Structure is {} on a {} multi-timeframe "
            "backdrop, and the fused evidence score of {:+.2f} is not enough to justify "
            "risk. Waiting for either a decisive close through a key level or for the "
            "conflicting components to resolve."
        ).format(breakdown.reason + ".", struct.structure_label,
                 mtf.aligned_bias.value, breakdown.directional_score)
        invalidation = "N/A — no position. Re-evaluate on the next bar close."
    else:
        direction = "long" if decision == Decision.BUY else "short"
        summary = (
            "{} setup on a {} structure with {:.0f}% component agreement. Entry around "
            "{:.{d}f} with invalidation at {:.{d}f} ({} pips) and first target {:.{d}f}, "
            "giving {:.2f}R. {}"
        ).format(
            direction.title(), struct.structure_label, breakdown.agreement * 100,
            plan.entry or 0.0, plan.stop_loss or 0.0, plan.risk_pips or 0.0,
            plan.take_profit or 0.0, plan.risk_reward or 0.0,
            breakdown.reason + ".", d=digits)
        invalidation = "A close beyond {:.{d}f} invalidates this read.".format(
            plan.stop_loss or 0.0, d=digits)

    uncertainty_bits = []
    if breakdown.conflict:
        uncertainty_bits.append(breakdown.reason)
    if mtf.conflicts:
        uncertainty_bits.append("Timeframes disagree: " + "; ".join(mtf.conflicts[:2]))
    if snap.relative_volume is not None and snap.relative_volume < 0.7:
        uncertainty_bits.append(
            "Volume is thin, so levels are less reliable than usual")
    if plan.spread_pips and plan.risk_pips and plan.spread_pips > plan.risk_pips * 0.12:
        uncertainty_bits.append(
            "Spread is {:.1f} pips against a {:.1f} pip stop — a material cost drag"
            .format(plan.spread_pips, plan.risk_pips))
    if not uncertainty_bits:
        uncertainty_bits.append(
            "Main risk is a liquidity sweep through the invalidation level before the "
            "move develops")

    key_levels = [v for v in (
        plan.entry, plan.stop_loss, plan.take_profit,
        struct.range_high, struct.range_low,
    ) if v is not None]

    return AIVerdict(
        decision=decision,
        confidence=round(breakdown.final_confidence, 1),
        probability=probability(breakdown, plan.risk_reward),
        entry=plan.entry,
        stop_loss=plan.stop_loss,
        take_profit=plan.take_profit,
        take_profit_2=plan.take_profit_2,
        risk_reward=plan.risk_reward,
        trend=mtf.aligned_bias.value,
        market_structure="{} ({})".format(struct.structure_label, struct.premium_discount),
        bullish_points=bull,
        bearish_points=bear,
        key_levels=[round(v, digits) for v in key_levels][:6],
        invalidation=invalidation,
        timeframe_alignment=mtf.narrative,
        summary=summary,
        uncertainty=" ".join(uncertainty_bits)[:1000],
        source="rule_engine",
        model=reason or "deterministic confluence engine",
        latency_ms=0,
    )
