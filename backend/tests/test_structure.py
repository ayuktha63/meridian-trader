from __future__ import annotations

import numpy as np

from app.analysis import structure as st
from app.schemas.analysis import Bias
from conftest import make_df, trending_df


def _zigzag(legs, base=1.10, step=200):
    """Build a series that walks through the given leg targets."""
    closes = [base]
    for target in legs:
        closes.extend(np.linspace(closes[-1], target, step)[1:])
    return np.asarray(closes)


def test_find_swings_labels_higher_highs_and_lows():
    closes = _zigzag([1.12, 1.105, 1.14, 1.125, 1.16], step=40)
    df = make_df(closes)
    swings = st.find_swings(df, 3, 3)
    labels = [s.label for s in swings if s.label]
    assert "HH" in labels
    assert "HL" in labels


def test_downtrend_labels_lower_highs_and_lows():
    closes = _zigzag([1.08, 1.095, 1.06, 1.075, 1.04], step=40)
    df = make_df(closes)
    swings = st.find_swings(df, 3, 3)
    labels = [s.label for s in swings if s.label]
    assert "LL" in labels
    assert "LH" in labels


def test_swings_never_use_unconfirmed_bars():
    """A swing must have `right` bars after it before it can be reported."""
    df = trending_df(200)
    swings = st.find_swings(df, 3, 3)
    assert all(s.index <= len(df) - 1 - 3 for s in swings)


def test_bos_and_choch_are_emitted():
    closes = _zigzag([1.13, 1.11, 1.16, 1.14, 1.19], step=35)
    df = make_df(closes)
    swings = st.find_swings(df, 3, 3)
    events, trend = st.detect_structure_events(df, swings, 3, atr=0.002)
    kinds = {e.kind for e in events}
    assert "BOS" in kinds or "CHoCH" in kinds
    assert trend in (Bias.BULLISH, Bias.BEARISH, Bias.NEUTRAL)


def test_bullish_fair_value_gap_detected():
    # Bar 2's low sits above bar 0's high — a textbook bullish imbalance.
    opens = [1.1000, 1.1010, 1.1080, 1.1090]
    closes = [1.1005, 1.1070, 1.1095, 1.1100]
    highs = [1.1008, 1.1075, 1.1099, 1.1105]
    lows = [1.0998, 1.1008, 1.1085, 1.1088]
    df = make_df(closes, highs, lows, opens)
    gaps = st.find_fair_value_gaps(df, atr=0.0010, min_size_atr=0.05)
    bullish = [g for g in gaps if g.direction == "bullish"]
    assert bullish
    assert bullish[0].bottom <= bullish[0].top


def test_equal_highs_cluster():
    closes = _zigzag([1.12, 1.10, 1.1201, 1.10, 1.1199], step=30)
    df = make_df(closes)
    swings = st.find_swings(df, 3, 3)
    highs, lows = st.find_equal_levels(swings, atr=0.004, tolerance_atr=0.5)
    assert highs or lows


def test_premium_discount_reflects_position_in_range():
    rising = make_df(np.linspace(1.05, 1.15, 250))
    result = st.analyse(rising, __import__("app.instruments", fromlist=["x"]).get_instrument("EURUSD"))
    assert result.premium_discount == "premium"

    falling = make_df(np.linspace(1.15, 1.05, 250))
    result = st.analyse(falling, __import__("app.instruments", fromlist=["x"]).get_instrument("EURUSD"))
    assert result.premium_discount == "discount"


def test_analyse_populates_full_report(eurusd, uptrend):
    result = st.analyse(uptrend, eurusd)
    assert result.structure_label in ("uptrend", "downtrend", "ranging", "transition")
    assert result.range_high is not None and result.range_low is not None
    assert result.range_high > result.range_low
    assert result.equilibrium is not None
    assert result.premium_discount in ("premium", "discount", "equilibrium")
    for zone in result.order_blocks + result.fair_value_gaps + result.supply_zones:
        assert zone.top >= zone.bottom


def test_analyse_handles_short_series(eurusd):
    result = st.analyse(make_df(np.linspace(1.1, 1.11, 10)), eurusd)
    assert result.notes
    assert result.structure_label == "ranging"


def test_summarise_is_json_safe(eurusd, uptrend):
    import json

    result = st.analyse(uptrend, eurusd)
    json.dumps(st.summarise(result, eurusd.digits))
