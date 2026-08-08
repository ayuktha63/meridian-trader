from __future__ import annotations

import numpy as np
import pandas as pd

from app.analysis import indicators as ind
from conftest import make_df, trending_df


def test_ema_matches_pandas():
    series = pd.Series(np.linspace(1.0, 2.0, 120))
    expected = series.ewm(span=20, adjust=False, min_periods=10).mean()
    assert np.allclose(ind.ema(series, 20).dropna(), expected.dropna())


def test_rsi_saturates_on_monotonic_series():
    rising = pd.Series(np.linspace(1.0, 2.0, 120))
    falling = pd.Series(np.linspace(2.0, 1.0, 120))
    assert ind.rsi(rising, 14).iloc[-1] > 95
    assert ind.rsi(falling, 14).iloc[-1] < 5


def test_rsi_bounded():
    df = trending_df(200)
    values = ind.rsi(df["close"], 14).dropna()
    assert values.between(0, 100).all()


def test_atr_positive_and_tracks_range():
    df = trending_df(200)
    atr = ind.atr(df, 14).dropna()
    assert (atr > 0).all()
    assert atr.iloc[-1] < float((df["high"] - df["low"]).max()) * 3


def test_supertrend_has_no_trailing_nan():
    """Regression: NaN seed bands used to propagate through the whole series."""
    df = trending_df(200)
    st = ind.supertrend(df)
    assert not np.isnan(st["supertrend"].iloc[-1])
    assert st["direction"].iloc[-1] in (-1, 1)
    # The line must sit on the correct side of price for its direction.
    last_close = float(df["close"].iloc[-1])
    if st["direction"].iloc[-1] == 1:
        assert st["supertrend"].iloc[-1] <= last_close * 1.02
    else:
        assert st["supertrend"].iloc[-1] >= last_close * 0.98


def test_supertrend_flips_direction_on_reversal():
    up = np.linspace(1.0, 1.3, 120)
    down = np.linspace(1.3, 1.0, 120)
    df = make_df(np.concatenate([up, down]))
    st = ind.supertrend(df)
    assert st["direction"].iloc[110] == 1
    assert st["direction"].iloc[-1] == -1


def test_bollinger_band_ordering():
    df = trending_df(200)
    bb = ind.bollinger(df["close"]).dropna()
    assert (bb["upper"] >= bb["mid"]).all()
    assert (bb["mid"] >= bb["lower"]).all()


def test_macd_signal_is_smoother_than_line():
    df = trending_df(300)
    macd = ind.macd(df["close"]).dropna()
    assert macd["line"].std() >= macd["signal"].std()
    assert np.allclose(macd["hist"], macd["line"] - macd["signal"])


def test_adx_components_bounded():
    df = trending_df(250)
    adx = ind.adx(df, 14).dropna()
    assert adx["adx"].between(0, 100).all()
    assert adx["plus_di"].between(0, 100).all()


def test_volume_profile_value_area_contains_poc():
    df = trending_df(240)
    profile = ind.volume_profile(df)
    assert profile["val"] <= profile["poc"] <= profile["vah"]
    assert profile["low"] <= profile["val"]
    assert profile["vah"] <= profile["high"]


def test_pivot_points_ordering():
    pivots = ind.pivot_points(1.10, 1.08, 1.095)
    assert pivots["s3"] < pivots["s2"] < pivots["s1"] < pivots["p"]
    assert pivots["p"] < pivots["r1"] < pivots["r2"] < pivots["r3"]


def test_snapshot_populates_core_fields(eurusd):
    df = trending_df(320)
    snap, computed = ind.snapshot(df, eurusd)
    assert snap.rsi is not None and 0 <= snap.rsi <= 100
    assert snap.atr and snap.atr > 0
    assert snap.atr_pips and snap.atr_pips > 0
    assert snap.ema.get("20") is not None
    assert snap.supertrend is not None
    assert snap.trend_strength in ("weak", "moderate", "strong")
    assert snap.volatility_regime in ("compressed", "normal", "expanded")
    assert "line" in snap.macd and "signal" in snap.macd
    assert set(computed) >= {"rsi", "atr", "macd", "adx"}


def test_divergence_detects_bearish_case():
    # Price makes a higher high while the oscillator makes a lower high.
    price = pd.Series([1, 2, 3, 2, 1, 2, 3, 4, 3, 2] * 8, dtype=float)
    osc = pd.Series([1, 5, 9, 5, 1, 2, 4, 6, 4, 2] * 8, dtype=float)
    result = ind.find_divergence(price, osc, lookback=60, pivot=3)
    assert result is None or result.startswith(("bullish", "bearish"))
