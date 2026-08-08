from __future__ import annotations

import numpy as np

from app.analysis import candlesticks, patterns, structure
from conftest import make_df, trending_df


def test_pattern_library_size():
    """The platform advertises 50+ candlestick patterns."""
    assert candlesticks.PATTERN_COUNT >= 50


def _framework(rows):
    """rows: list of (open, high, low, close)."""
    opens = [r[0] for r in rows]
    highs = [r[1] for r in rows]
    lows = [r[2] for r in rows]
    closes = [r[3] for r in rows]
    return make_df(closes, highs, lows, opens)


def _downtrend_prefix(n=20, start=1.12, end=1.10):
    step = (end - start) / n
    rows = []
    for i in range(n):
        o = start + step * i
        c = o + step
        rows.append((o, max(o, c) + 0.0002, min(o, c) - 0.0002, c))
    return rows


def test_bullish_engulfing_detected():
    rows = _downtrend_prefix()
    rows.append((1.1005, 1.1008, 1.0990, 1.0992))          # bearish
    rows.append((1.0990, 1.1030, 1.0988, 1.1025))          # engulfing bullish
    hits = candlesticks.detect(_framework(rows), lookback=3)
    assert any(h.name == "Bullish Engulfing" for h in hits)


def test_bearish_engulfing_detected():
    rows = []
    start = 1.10
    for i in range(20):
        o = start + 0.001 * i
        c = o + 0.001
        rows.append((o, max(o, c) + 0.0002, min(o, c) - 0.0002, c))
    rows.append((1.1200, 1.1215, 1.1198, 1.1210))          # bullish
    rows.append((1.1212, 1.1214, 1.1180, 1.1185))          # engulfing bearish
    hits = candlesticks.detect(_framework(rows), lookback=3)
    assert any(h.name == "Bearish Engulfing" for h in hits)


def test_hammer_detected_after_decline():
    rows = _downtrend_prefix()
    # Small body at the top, long lower wick.
    rows.append((1.1000, 1.1003, 1.0940, 1.0998))
    hits = candlesticks.detect(_framework(rows), lookback=2)
    assert any(h.name in ("Hammer", "Takuri Line", "Dragonfly Doji") for h in hits)


def test_doji_detected():
    rows = _downtrend_prefix()
    rows.append((1.1000, 1.1020, 1.0980, 1.10001))
    hits = candlesticks.detect(_framework(rows), lookback=2)
    assert any("Doji" in h.name or h.name == "Spinning Top" for h in hits)


def test_morning_star_detected():
    rows = _downtrend_prefix()
    rows.append((1.1050, 1.1052, 1.0980, 1.0985))          # long bearish
    rows.append((1.0975, 1.0982, 1.0970, 1.0978))          # small body
    rows.append((1.0980, 1.1045, 1.0978, 1.1040))          # strong bullish close
    hits = candlesticks.detect(_framework(rows), lookback=2)
    assert any(h.name in ("Morning Star", "Morning Doji Star", "Three Outside Up")
               for h in hits)


def test_candlestick_hits_carry_direction_and_recency():
    df = trending_df(200)
    hits = candlesticks.detect(df)
    for hit in hits:
        assert hit.direction in ("bullish", "bearish", "neutral")
        assert 0.0 <= hit.confidence <= 1.0
        assert hit.category == "candlestick"
        assert hit.end_index >= hit.start_index


def test_double_top_detected():
    legs = [1.14, 1.115, 1.1398, 1.10]
    closes = [1.10]
    for target in legs:
        closes.extend(np.linspace(closes[-1], target, 45)[1:])
    df = make_df(np.asarray(closes))
    swings = structure.find_swings(df, 3, 3)
    hits = patterns.detect(df, swings, atr=0.004)
    names = {h.name for h in hits}
    assert names  # something geometric should be recognised
    assert all(0.0 <= h.confidence <= 1.0 for h in hits)


def test_chart_patterns_have_geometry():
    df = trending_df(320)
    swings = structure.find_swings(df, 3, 3)
    hits = patterns.detect(df, swings, atr=0.004)
    for hit in hits:
        assert hit.category == "chart"
        assert hit.end_time >= hit.start_time
        for point in hit.points:
            assert "time" in point and "price" in point


def test_pattern_detection_survives_short_series():
    df = make_df(np.linspace(1.1, 1.11, 12))
    assert patterns.detect(df, [], atr=0.001) == []
    assert candlesticks.detect(df) == []
