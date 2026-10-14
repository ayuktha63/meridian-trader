"""The indicators the strategies and the live engine use, against definitions
written out longhand.

`pandas-ta` and TA-Lib are not installed here (pandas-ta's repository is gone
and its PyPI release needs Python 3.12; this suite runs on 3.9), so the
references below are plain loops from each indicator's textbook definition.
They share no code with `app/analysis/indicators.py`.

Where the engine's definition differs from the textbook one, the difference is
stated and measured, not hidden: Wilder's smoothing is seeded here with the
first value (`ewm(adjust=False)`), the textbook seeds it with the mean of the
first `n`. The two converge geometrically — after k bars they differ by a
factor (1 - 1/n)^k — so they are compared after a warm-up long enough for the
gap to be below the tolerance, and the warm-up needed is asserted."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.analysis import indicators as ind


def bars(n: int = 700, seed: int = 4, start: float = 1500.0, step: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(0.0001, 0.003, n)))
    open_ = np.r_[start, close[:-1]] * (1 + rng.normal(0, 0.0007, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.0012, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.0012, n)))
    vol = rng.integers(1_000, 90_000, n).astype(float)
    time = 1_700_000_000.0 + step * np.arange(n)
    return pd.DataFrame({"time": time, "open": open_, "high": high, "low": low,
                         "close": close, "volume": vol})


# ── longhand references ───────────────────────────────────────

def ref_ema(x, n):
    a, out = 2.0 / (n + 1), []
    for i, v in enumerate(x):
        out.append(v if i == 0 else a * v + (1 - a) * out[-1])
    return np.array(out)


def ref_wilder_textbook(x, n):
    """Mean of the first n, then y = (y*(n-1) + x)/n. NaN before the n-th value."""
    out = np.full(len(x), np.nan)
    out[n - 1] = float(np.mean(x[:n]))
    for i in range(n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def ref_tr(h, l, c):
    out = [h[0] - l[0]]
    for i in range(1, len(h)):
        out.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return np.array(out)


def ref_rsi(c, n):
    gains, losses = [0.0], [0.0]
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    # the first difference does not exist: Wilder starts at index 1
    g = ref_wilder_textbook(np.array(gains[1:]), n)
    l = ref_wilder_textbook(np.array(losses[1:]), n)
    rs = g / l
    return np.r_[np.nan, 100 - 100 / (1 + rs)]


def ref_adx(h, l, c, n):
    pdm, mdm = [0.0], [0.0]
    for i in range(1, len(h)):
        up, down = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(up if (up > down and up > 0) else 0.0)
        mdm.append(down if (down > up and down > 0) else 0.0)
    tr = ref_tr(h, l, c)
    s_tr, s_p, s_m = (ref_wilder_textbook(np.array(v[1:]), n) for v in (tr, pdm, mdm))
    pdi, mdi = 100 * s_p / s_tr, 100 * s_m / s_tr
    dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    first = int(np.flatnonzero(~np.isnan(dx))[0])
    adx = np.full(len(dx), np.nan)
    adx[first + n - 1] = float(np.mean(dx[first:first + n]))
    for i in range(first + n, len(dx)):
        adx[i] = (adx[i - 1] * (n - 1) + dx[i]) / n
    pad = lambda v: np.r_[np.nan, v]  # noqa: E731
    return pad(adx), pad(pdi), pad(mdi)


def ref_vwap(df):
    out, pv, v, day = [], 0.0, 0.0, None
    for t, h, l, c, vol in zip(df["time"], df["high"], df["low"], df["close"], df["volume"]):
        d = int(t // 86400)
        if d != day:
            pv = v = 0.0
            day = d
        tp, w = (h + l + c) / 3.0, (vol if vol > 0 else 1.0)
        pv += tp * w
        v += w
        out.append(pv / v)
    return np.array(out)


def close_to(got, want, tol, skip=0):
    got, want = np.asarray(got, dtype=float)[skip:], np.asarray(want, dtype=float)[skip:]
    assert np.allclose(got, want, rtol=tol, atol=tol, equal_nan=False), \
        "max abs gap {:.3e}".format(np.nanmax(np.abs(got - want)))


# ── exact definitions ─────────────────────────────────────────

def test_ema_is_the_recursion_seeded_with_the_first_value():
    df = bars()
    for n in (9, 21, 50):
        got = ind.ema(df["close"], n)
        close_to(got.to_numpy()[n:], ref_ema(df["close"].to_numpy(), n)[n:], 1e-12)


def test_ema_is_nan_before_half_a_window_and_never_after():
    got = ind.ema(bars()["close"], 20)
    assert got.iloc[:9].isna().all() and got.iloc[9:].notna().all()


def test_sma_and_bollinger_are_the_window_mean_and_population_std():
    df = bars()
    c = df["close"].to_numpy()
    bb = ind.bollinger(df["close"], 20, 2.0)
    for i in (19, 100, 400, len(c) - 1):
        w = c[i - 19:i + 1]
        mid, sd = w.mean(), math.sqrt(((w - w.mean()) ** 2).mean())
        assert bb["mid"].iloc[i] == pytest.approx(mid, rel=1e-12)
        assert bb["upper"].iloc[i] == pytest.approx(mid + 2 * sd, rel=1e-10)
        assert bb["lower"].iloc[i] == pytest.approx(mid - 2 * sd, rel=1e-10)
    assert ind.sma(df["close"], 20).iloc[19] == pytest.approx(c[:20].mean(), rel=1e-12)


def test_macd_is_the_difference_of_two_emas_and_its_own_signal():
    df = bars()
    c = df["close"].to_numpy()
    m = ind.macd(df["close"])
    line = ref_ema(c, 12) - ref_ema(c, 26)
    close_to(m["line"].to_numpy()[26:], line[26:], 1e-12)
    # The engine seeds the signal on the first *defined* MACD value (bar 12), the
    # reference on bar 0; the seeds differ by a factor 0.8^k, gone by bar 200.
    close_to(m["signal"].to_numpy()[200:], ref_ema(line, 9)[200:], 1e-12)
    close_to(m["hist"].to_numpy()[200:], (line - ref_ema(line, 9))[200:], 1e-12)


def test_true_range_is_the_largest_of_the_three_distances():
    df = bars(200)
    got = ind.true_range(df).to_numpy()
    want = ref_tr(df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy())
    close_to(got[1:], want[1:], 1e-12)


def test_vwap_resets_each_day_and_counts_a_zero_volume_bar_as_one():
    df = bars(400, step=900)
    df.loc[[3, 150], "volume"] = 0.0
    close_to(ind.vwap(df)["vwap"], ref_vwap(df), 1e-10)
    days = (df["time"] // 86400).astype(int)
    assert days.nunique() > 3, "the fixture must span several sessions"


# ── the textbook difference, measured ─────────────────────────

def test_wilder_seeded_with_the_first_value_converges_to_the_textbook_one():
    df = bars(900)
    x = df["close"].diff().abs().fillna(0.0).to_numpy()[1:]
    for n in (7, 14, 21):
        mine = ind.wilder(pd.Series(x), n).to_numpy()
        book = ref_wilder_textbook(x, n)
        gap = np.abs(mine - book) / np.maximum(np.abs(book), 1e-9)
        # the seeds differ by at most the spread of the first n values, shrinking by (1-1/n) a bar
        needed = int(math.ceil(math.log(1e-6) / math.log(1 - 1 / n))) + n
        assert needed < 800
        assert np.nanmax(gap[needed:]) < 1e-5, (n, needed)


@pytest.mark.parametrize("n", [7, 14])
def test_rsi_matches_the_textbook_after_warmup(n):
    df = bars(900)
    c = df["close"].to_numpy()
    warm = 40 * n
    close_to(ind.rsi(df["close"], n).to_numpy(), ref_rsi(c, n), 1e-6, skip=warm)


def test_atr_matches_the_textbook_after_warmup():
    df = bars(900)
    h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    want = np.r_[np.nan, ref_wilder_textbook(ref_tr(h, l, c)[1:], 14)]
    close_to(ind.atr(df, 14).to_numpy(), want, 1e-6, skip=600)


def test_adx_and_directional_indices_match_the_textbook_after_warmup():
    df = bars(1200)
    h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    adx, pdi, mdi = ref_adx(h, l, c, 14)
    got = ind.adx(df, 14)
    close_to(got["plus_di"].to_numpy(), pdi, 1e-5, skip=700)
    close_to(got["minus_di"].to_numpy(), mdi, 1e-5, skip=700)
    close_to(got["adx"].to_numpy(), adx, 1e-4, skip=900)


# ── properties every adopted indicator must keep ──────────────

CASES = {
    "ema": lambda d: ind.ema(d["close"], 21),
    "rsi": lambda d: ind.rsi(d["close"], 14),
    "atr": lambda d: ind.atr(d, 14),
    "adx": lambda d: ind.adx(d, 14)["adx"],
    "macd": lambda d: ind.macd(d["close"])["hist"],
    "bollinger": lambda d: ind.bollinger(d["close"])["pctb"],
    "supertrend": lambda d: ind.supertrend(d, 10, 3.0)["supertrend"],
    "vwap": lambda d: ind.vwap(d)["vwap"],
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_value_never_depends_on_bars_after_it(name):
    """Cut the history anywhere: everything computed before the cut is
    unchanged. Computing on the whole series and reading the old rows is what
    a backtest does; computing on the cut one is what live trading has."""
    df = bars(500)
    full = CASES[name](df).to_numpy(dtype=float)
    for cut in (60, 137, 300, 499):
        part = CASES[name](df.iloc[:cut].reset_index(drop=True)).to_numpy(dtype=float)
        assert np.allclose(full[:cut], part, equal_nan=True, rtol=1e-12, atol=1e-12), (name, cut)


@pytest.mark.parametrize("name", sorted(CASES))
def test_too_little_history_gives_nan_not_an_error_or_a_number(name):
    df = bars(5)
    out = CASES[name](df).to_numpy(dtype=float)
    if name in ("ema", "vwap", "macd", "bollinger", "supertrend"):
        assert len(out) == 5                      # defined from the first bars (half-window rule)
    else:
        assert np.isnan(out).all(), name           # RSI, ATR, ADX need their full window


@pytest.mark.parametrize("name", ["ema", "rsi", "atr", "macd"])
def test_a_nan_bar_does_not_poison_everything_after_it(name):
    df = bars(500)
    df.loc[200, ["open", "high", "low", "close"]] = np.nan
    out = CASES[name](df).to_numpy(dtype=float)
    assert np.isfinite(out[400:]).all(), "later values must recover"
    assert np.isfinite(out[:199][~np.isnan(out[:199])]).all()


def test_indicators_do_not_change_their_input():
    df = bars(300)
    before = df.copy()
    for fn in CASES.values():
        fn(df)
    pd.testing.assert_frame_equal(df, before)


def test_rsi_extremes_are_100_for_no_losses_and_50_for_a_flat_market():
    up = pd.Series(np.linspace(100, 200, 80))
    assert ind.rsi(up, 14).iloc[-1] == pytest.approx(100.0)
    flat = pd.Series([100.0] * 80)
    assert ind.rsi(flat, 14).iloc[-1] == pytest.approx(50.0)
