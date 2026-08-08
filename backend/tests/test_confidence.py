from __future__ import annotations

from app.analysis import confidence
from app.schemas.analysis import (
    AIVerdict, Bias, ConfidenceBreakdown, ConfidenceComponent, Decision,
    IndicatorSnapshot, MarketStructure, MTFReport, TimeframeView,
)


def _breakdown(**raw) -> ConfidenceBreakdown:
    """Assemble a breakdown directly so decision rules can be tested in isolation."""
    components = [
        ConfidenceComponent(name=name, weight=confidence.WEIGHTS[name],
                            raw_score=raw.get(name, 0.0),
                            contribution=raw.get(name, 0.0) * confidence.WEIGHTS[name] * 100)
        for name in confidence.WEIGHTS
    ]
    participating = [c for c in components if c.name != "ai" or raw.get("ai")]
    weight = sum(c.weight for c in participating)
    directional = sum(c.raw_score * c.weight for c in participating) / weight
    meaningful = [c for c in participating if abs(c.raw_score) > 0.12]
    if meaningful and abs(directional) > 1e-9:
        dominant = 1 if directional > 0 else -1
        agreeing = sum(c.weight for c in meaningful
                       if (1 if c.raw_score > 0 else -1) == dominant)
        agreement = agreeing / sum(c.weight for c in meaningful)
    else:
        agreement = 0.0
    final = confidence.score_to_confidence(directional, agreement)
    return ConfidenceBreakdown(
        components=components, directional_score=directional, agreement=agreement,
        final_confidence=final,
        conflict=agreement < confidence.MIN_AGREEMENT
        or abs(directional) < confidence.DECISION_THRESHOLD,
        reason="test",
    )


def test_weights_sum_to_one():
    assert abs(sum(confidence.WEIGHTS.values()) - 1.0) < 1e-9
    assert confidence.WEIGHTS["trend"] == 0.25
    assert confidence.WEIGHTS["momentum"] == 0.20
    assert confidence.WEIGHTS["volume"] == 0.15
    assert confidence.WEIGHTS["structure"] == 0.20
    assert confidence.WEIGHTS["pattern"] == 0.10
    assert confidence.WEIGHTS["ai"] == 0.10


def test_aligned_bullish_evidence_produces_buy():
    breakdown = _breakdown(trend=0.8, momentum=0.7, volume=0.6,
                           structure=0.8, pattern=0.6)
    assert not breakdown.conflict
    assert breakdown.final_confidence >= confidence.MIN_CONFIDENCE
    assert confidence.decide(breakdown) == Decision.BUY


def test_aligned_bearish_evidence_produces_sell():
    breakdown = _breakdown(trend=-0.8, momentum=-0.7, volume=-0.6,
                           structure=-0.8, pattern=-0.6)
    assert confidence.decide(breakdown) == Decision.SELL


def test_split_evidence_forces_wait():
    """Strong but opposed components must not average into a directional call."""
    breakdown = _breakdown(trend=0.9, momentum=-0.9, volume=0.8,
                           structure=-0.85, pattern=0.5)
    assert breakdown.conflict
    assert confidence.decide(breakdown) == Decision.WAIT


def test_weak_but_agreeing_evidence_still_waits():
    breakdown = _breakdown(trend=0.15, momentum=0.12, volume=0.1,
                           structure=0.14, pattern=0.1)
    assert confidence.decide(breakdown) == Decision.WAIT


def test_confidence_scales_with_agreement():
    aligned = _breakdown(trend=0.6, momentum=0.6, volume=0.6,
                         structure=0.6, pattern=0.6)
    mixed = _breakdown(trend=0.6, momentum=-0.3, volume=0.6,
                       structure=-0.3, pattern=0.6)
    assert aligned.final_confidence > mixed.final_confidence


def test_probability_is_bounded_and_conservative():
    strong = _breakdown(trend=1.0, momentum=1.0, volume=1.0,
                        structure=1.0, pattern=1.0)
    assert confidence.probability(strong) <= 72.0
    assert confidence.probability(strong, risk_reward=5.0) < confidence.probability(strong, 2.0)
    weak = _breakdown(trend=0.1)
    assert confidence.probability(weak) >= 30.0


def test_ai_component_only_participates_when_consulted():
    snap = IndicatorSnapshot(rsi=60.0, adx=30.0, di_plus=30.0, di_minus=10.0)
    structure = MarketStructure(trend=Bias.BULLISH, structure_label="uptrend")
    mtf = MTFReport(views=[TimeframeView(timeframe="1h", score=0.5)],
                    weighted_score=0.5, alignment_score=1.0,
                    aligned_bias=Bias.BULLISH)

    without = confidence.compute(snap, structure, mtf, [], [], 1.10)
    assert any(c.name == "ai" and c.raw_score == 0.0 for c in without.components)

    verdict = AIVerdict(decision=Decision.BUY, confidence=80.0)
    with_ai = confidence.compute(snap, structure, mtf, [], [], 1.10, verdict=verdict)
    ai_component = next(c for c in with_ai.components if c.name == "ai")
    assert ai_component.raw_score > 0
    assert with_ai.directional_score > without.directional_score


def test_compute_returns_all_six_components():
    snap = IndicatorSnapshot(rsi=55.0)
    breakdown = confidence.compute(snap, MarketStructure(), MTFReport(), [], [], 1.10)
    names = {c.name for c in breakdown.components}
    assert names == set(confidence.WEIGHTS)
    assert -1.0 <= breakdown.directional_score <= 1.0
    assert 0.0 <= breakdown.final_confidence <= 100.0
    assert breakdown.reason
