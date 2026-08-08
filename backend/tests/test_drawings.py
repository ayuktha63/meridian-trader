from __future__ import annotations

import time

import numpy as np

from app.analysis import drawings as engine
from app.schemas.drawings import Drawing, DrawingStyle, DrawingType, Point
from conftest import BAR, make_df


def _drawing(dtype: DrawingType, points, **kwargs) -> Drawing:
    now = time.time()
    return Drawing(
        id="test-drawing", symbol="EURUSD", timeframe="15m", type=dtype,
        points=[Point(time=t, price=p) for t, p in points],
        style=DrawingStyle(), created_at=now, updated_at=now, **kwargs
    )


def _oscillating(level: float, touches: int, bars_per_leg: int = 12):
    """Price that repeatedly rallies into `level` and rejects from it."""
    closes = [level - 0.004]
    for _ in range(touches):
        closes.extend(np.linspace(closes[-1], level - 0.0002, bars_per_leg)[1:])
        closes.extend(np.linspace(closes[-1], level - 0.005, bars_per_leg)[1:])
    return np.asarray(closes)


def test_resistance_counts_touches_and_reactions(eurusd):
    level = 1.1100
    closes = _oscillating(level, touches=4)
    n = len(closes)
    highs = closes + 0.0004
    lows = closes - 0.0004
    # Make each approach actually tag the level.
    for i, c in enumerate(closes):
        if abs(c - (level - 0.0002)) < 0.0003:
            highs[i] = level + 0.0002
    df = make_df(closes, highs, lows)

    start = int(df["time"].iloc[0])
    drawing = _drawing(DrawingType.RESISTANCE,
                       [(start, level), (start + BAR * (n - 1), level)])
    result = engine.analyse_drawing(drawing, df, eurusd, atr=0.0012)

    assert result.role == "resistance"
    assert result.touches >= 2
    assert result.current_value == round(level, eurusd.digits)
    assert result.strength > 0
    assert result.validity in ("respected", "untested", "broken", "invalidated")
    assert result.notes


def test_break_is_recorded_and_flips_validity(eurusd):
    level = 1.1100
    closes = np.concatenate([
        np.linspace(1.1050, level - 0.0005, 40),
        np.linspace(level - 0.0005, 1.1200, 40),      # decisive break through
    ])
    df = make_df(closes, closes + 0.0004, closes - 0.0004)
    start = int(df["time"].iloc[0])
    drawing = _drawing(DrawingType.RESISTANCE,
                       [(start, level), (int(df["time"].iloc[-1]), level)])
    result = engine.analyse_drawing(drawing, df, eurusd, atr=0.0010)

    assert result.breaks >= 1
    assert result.validity in ("broken", "invalidated")
    assert result.price_side == "above"


def test_sloped_trendline_projects_forward(eurusd):
    closes = np.linspace(1.1000, 1.1200, 120)
    df = make_df(closes, closes + 0.0003, closes - 0.0003)
    t0 = int(df["time"].iloc[0])
    t1 = int(df["time"].iloc[60])
    drawing = _drawing(DrawingType.TRENDLINE,
                       [(t0, 1.0990), (t1, 1.1090)])
    result = engine.analyse_drawing(drawing, df, eurusd, atr=0.0008)

    assert result.slope_per_bar > 0
    assert result.angle_degrees != 0
    # Projected to the final bar, the line should be above where it started.
    assert result.current_value > 1.1090
    assert result.length_bars == 60


def test_angle_is_scale_invariant(eurusd):
    """ATR-normalised angle means the same thing regardless of price scale."""
    closes = np.linspace(1.1000, 1.1200, 120)
    df = make_df(closes, closes + 0.0003, closes - 0.0003)
    t0, t1 = int(df["time"].iloc[0]), int(df["time"].iloc[60])
    slope_line = _drawing(DrawingType.TRENDLINE, [(t0, 1.0990), (t1, 1.1090)])
    small = engine.analyse_drawing(slope_line, df, eurusd, atr=0.0008)

    scaled = df.copy()
    for column in ("open", "high", "low", "close"):
        scaled[column] = scaled[column] * 1000
    big_line = _drawing(DrawingType.TRENDLINE, [(t0, 1099.0), (t1, 1109.0)])
    big = engine.analyse_drawing(big_line, scaled, eurusd, atr=0.8)

    assert abs(small.angle_degrees - big.angle_degrees) < 5.0


def test_zone_reports_inside_when_price_is_within(eurusd):
    closes = np.full(80, 1.1050)
    df = make_df(closes, closes + 0.0005, closes - 0.0005)
    start = int(df["time"].iloc[0])
    drawing = _drawing(DrawingType.DEMAND_ZONE,
                       [(start, 1.1030), (int(df["time"].iloc[-1]), 1.1070)])
    result = engine.analyse_drawing(drawing, df, eurusd, atr=0.0010)

    assert result.role == "demand"
    assert result.price_side == "inside"
    assert result.upper_value == 1.1070
    assert result.lower_value == 1.1030
    assert result.distance_pips == 0.0


def test_fibonacci_levels_are_computed(eurusd):
    closes = np.linspace(1.1000, 1.1200, 100)
    df = make_df(closes)
    t0, t1 = int(df["time"].iloc[0]), int(df["time"].iloc[-1])
    drawing = _drawing(DrawingType.FIB_RETRACEMENT, [(t0, 1.1000), (t1, 1.1200)])
    result = engine.analyse_drawing(drawing, df, eurusd, atr=0.0010)

    assert "0.618" in result.fib_levels
    assert "0.500" in result.fib_levels
    # 50% retracement of a 1.1000→1.1200 leg is 1.1100.
    assert abs(result.fib_levels["0.500"] - 1.1100) < 1e-6
    assert result.role == "measurement"


def test_risk_reward_box_reports_ratio(eurusd):
    closes = np.linspace(1.1000, 1.1100, 60)
    df = make_df(closes)
    t0 = int(df["time"].iloc[0])
    drawing = _drawing(DrawingType.RISK_REWARD,
                       [(t0, 1.1050), (t0 + BAR * 10, 1.1030), (t0 + BAR * 20, 1.1110)])
    result = engine.analyse_drawing(drawing, df, eurusd, atr=0.0010)
    assert any("R:R" in note for note in result.notes)


def test_text_note_is_surfaced_to_the_ai(eurusd):
    df = make_df(np.linspace(1.10, 1.11, 60))
    drawing = _drawing(DrawingType.TEXT, [(int(df["time"].iloc[10]), 1.105)],
                       text="Waiting for NFP before adding")
    result = engine.analyse_drawing(drawing, df, eurusd, atr=0.001)
    assert result.role == "annotation"
    assert any("NFP" in note for note in result.notes)


def test_analyse_many_isolates_failures(eurusd):
    df = make_df(np.linspace(1.10, 1.11, 60))
    good = _drawing(DrawingType.HORIZONTAL, [(int(df["time"].iloc[0]), 1.105)])
    broken = _drawing(DrawingType.TRENDLINE, [])
    results = engine.analyse_many([good, broken], df, eurusd, atr=0.001)
    assert len(results) == 2


def test_summarise_is_json_safe(eurusd):
    import json

    df = make_df(np.linspace(1.10, 1.11, 80))
    drawing = _drawing(DrawingType.SUPPORT, [(int(df["time"].iloc[0]), 1.100)])
    stats = engine.analyse_many([drawing], df, eurusd, atr=0.001)
    json.dumps(engine.summarise(stats, eurusd.digits))
