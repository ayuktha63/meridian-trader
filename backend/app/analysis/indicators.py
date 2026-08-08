"""Technical indicator engine.

Implemented natively in NumPy/Pandas so the platform never hard-depends on a
C toolchain (TA-Lib) that half of macOS installs fail to build. Where
pandas-ta or TA-Lib are installed they are only used for cross-validation in
the test suite — never at runtime, so results are identical everywhere.

Every function takes and returns pandas objects aligned to the input index.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..instruments import Instrument
from ..schemas.analysis import IndicatorSnapshot

EPS = 1e-12


# ── moving averages ───────────────────────────────────────────

def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length, min_periods=max(1, length // 2)).mean()


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=max(1, length // 2)).mean()


def wilder(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothing — the basis of RSI/ATR/ADX."""
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def hma(series: pd.Series, length: int) -> pd.Series:
    half, sqrt_len = int(length / 2), int(math.sqrt(length))
    wma = lambda s, n: s.rolling(n).apply(  # noqa: E731
        lambda x: np.dot(x, np.arange(1, n + 1)) / (n * (n + 1) / 2), raw=True
    )
    return wma(2 * wma(series, half) - wma(series, length), sqrt_len)


# ── volatility ────────────────────────────────────────────────

def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    return wilder(true_range(df), length)


def bollinger(series: pd.Series, length: int = 20, mult: float = 2.0) -> pd.DataFrame:
    mid = sma(series, length)
    sd = series.rolling(length, min_periods=max(1, length // 2)).std(ddof=0)
    upper, lower = mid + mult * sd, mid - mult * sd
    width = (upper - lower) / mid.replace(0, np.nan) * 100.0
    pctb = (series - lower) / (upper - lower).replace(0, np.nan)
    return pd.DataFrame({"upper": upper, "mid": mid, "lower": lower,
                         "width": width, "pctb": pctb})


def keltner(df: pd.DataFrame, length: int = 20, mult: float = 1.5) -> pd.DataFrame:
    mid = ema(df["close"], length)
    rng = atr(df, length) * mult
    return pd.DataFrame({"upper": mid + rng, "mid": mid, "lower": mid - rng})


# ── momentum ──────────────────────────────────────────────────

def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = wilder(delta.clip(lower=0.0), length)
    loss = wilder((-delta).clip(lower=0.0), length)

    out = 100.0 - (100.0 / (1.0 + gain / loss.replace(0, np.nan)))
    # With no losses in the window RS is infinite and RSI is 100; with neither
    # gains nor losses the market is flat, which is 50. Collapsing both to 50
    # (the naive fillna) silently hides genuine momentum extremes.
    saturated = pd.Series(np.where(gain > 0, 100.0, 50.0), index=series.index)
    out = out.where(loss > 0, saturated)
    return out.where(gain.notna() & loss.notna())


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(series, fast) - ema(series, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"line": line, "signal": sig, "hist": line - sig})


def stoch(df: pd.DataFrame, k: int = 14, d: int = 3, smooth: int = 3) -> pd.DataFrame:
    lowest = df["low"].rolling(k, min_periods=1).min()
    highest = df["high"].rolling(k, min_periods=1).max()
    raw = (df["close"] - lowest) / (highest - lowest).replace(0, np.nan) * 100.0
    k_line = raw.rolling(smooth, min_periods=1).mean()
    return pd.DataFrame({"k": k_line, "d": k_line.rolling(d, min_periods=1).mean()})


def stoch_rsi(series: pd.Series, rsi_len: int = 14, stoch_len: int = 14,
              k: int = 3, d: int = 3) -> pd.DataFrame:
    r = rsi(series, rsi_len)
    lowest = r.rolling(stoch_len, min_periods=1).min()
    highest = r.rolling(stoch_len, min_periods=1).max()
    raw = (r - lowest) / (highest - lowest).replace(0, np.nan) * 100.0
    k_line = raw.rolling(k, min_periods=1).mean()
    return pd.DataFrame({"k": k_line, "d": k_line.rolling(d, min_periods=1).mean()})


def cci(df: pd.DataFrame, length: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    ma = tp.rolling(length, min_periods=1).mean()
    md = (tp - ma).abs().rolling(length, min_periods=1).mean()
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def roc(series: pd.Series, length: int = 10) -> pd.Series:
    return series.pct_change(length) * 100.0


def williams_r(df: pd.DataFrame, length: int = 14) -> pd.Series:
    highest = df["high"].rolling(length, min_periods=1).max()
    lowest = df["low"].rolling(length, min_periods=1).min()
    return -100.0 * (highest - df["close"]) / (highest - lowest).replace(0, np.nan)


# ── trend ─────────────────────────────────────────────────────

def adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = wilder(true_range(df), length)
    plus_di = 100.0 * wilder(pd.Series(plus_dm, index=df.index), length) / tr.replace(0, np.nan)
    minus_di = 100.0 * wilder(pd.Series(minus_dm, index=df.index), length) / tr.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame({"adx": wilder(dx, length), "plus_di": plus_di, "minus_di": minus_di})


def supertrend(df: pd.DataFrame, length: int = 10, mult: float = 3.0) -> pd.DataFrame:
    hl2 = (df["high"] + df["low"]) / 2.0
    band = atr(df, length) * mult
    upper_basic = (hl2 + band).to_numpy()
    lower_basic = (hl2 - band).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)

    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)
    line = np.full(n, np.nan)

    for i in range(n):
        if np.isnan(upper_basic[i]):
            upper[i], lower[i] = upper_basic[i], lower_basic[i]
            continue
        # Seed from the basic bands on the first usable bar — carrying a NaN
        # forward here would poison every subsequent band.
        if i == 0 or np.isnan(upper[i - 1]) or np.isnan(lower[i - 1]):
            upper[i], lower[i] = upper_basic[i], lower_basic[i]
            direction[i] = 1 if close[i] >= upper_basic[i] else -1
            line[i] = lower[i] if direction[i] == 1 else upper[i]
            continue
        upper[i] = (upper_basic[i] if (upper_basic[i] < upper[i - 1] or close[i - 1] > upper[i - 1])
                    else upper[i - 1])
        lower[i] = (lower_basic[i] if (lower_basic[i] > lower[i - 1] or close[i - 1] < lower[i - 1])
                    else lower[i - 1])
        if close[i] > upper[i - 1]:
            direction[i] = 1
        elif close[i] < lower[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        line[i] = lower[i] if direction[i] == 1 else upper[i]

    return pd.DataFrame({"supertrend": line, "direction": direction,
                         "upper": upper, "lower": lower}, index=df.index)


def ichimoku(df: pd.DataFrame, conv: int = 9, base: int = 26,
             span_b: int = 52, displacement: int = 26) -> pd.DataFrame:
    def mid(length: int) -> pd.Series:
        return (df["high"].rolling(length, min_periods=1).max()
                + df["low"].rolling(length, min_periods=1).min()) / 2.0

    tenkan, kijun = mid(conv), mid(base)
    return pd.DataFrame({
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": ((tenkan + kijun) / 2.0).shift(displacement),
        "senkou_b": mid(span_b).shift(displacement),
        "chikou": df["close"].shift(-displacement),
    })


# ── volume ────────────────────────────────────────────────────

def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff().fillna(0.0))
    return (direction * df["volume"]).cumsum()


def mfi(df: pd.DataFrame, length: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    flow = tp * df["volume"]
    delta = tp.diff()
    pos = flow.where(delta > 0, 0.0).rolling(length, min_periods=1).sum()
    neg = flow.where(delta < 0, 0.0).rolling(length, min_periods=1).sum()
    ratio = pos / neg.replace(0, np.nan)
    return (100.0 - 100.0 / (1.0 + ratio)).fillna(50.0)


def vwap(df: pd.DataFrame, anchor_seconds: int = 86400) -> pd.DataFrame:
    """Session-anchored VWAP with ±1σ / ±2σ bands."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    session = (df["time"] // anchor_seconds).astype(int)
    vol = df["volume"].replace(0, np.nan).fillna(1.0)

    grouped_pv = (tp * vol).groupby(session).cumsum()
    grouped_v = vol.groupby(session).cumsum()
    line = grouped_pv / grouped_v.replace(0, np.nan)

    var = ((tp - line) ** 2 * vol).groupby(session).cumsum() / grouped_v.replace(0, np.nan)
    sd = np.sqrt(var.clip(lower=0))
    return pd.DataFrame({
        "vwap": line, "upper1": line + sd, "lower1": line - sd,
        "upper2": line + 2 * sd, "lower2": line - 2 * sd,
    })


def volume_profile(df: pd.DataFrame, bins: int = 48,
                   value_area: float = 0.70) -> Dict[str, Any]:
    """Price-binned volume distribution → POC, value area, HVN/LVN."""
    if df.empty or len(df) < 5:
        return {}
    low, high = float(df["low"].min()), float(df["high"].max())
    if high - low < EPS:
        return {}

    edges = np.linspace(low, high, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    hist = np.zeros(bins)

    lows = df["low"].to_numpy()
    highs = df["high"].to_numpy()
    vols = df["volume"].to_numpy()
    # Spread each bar's volume across the bins its range covers.
    for lo, hi, vol in zip(lows, highs, vols):
        start = np.searchsorted(edges, lo, side="right") - 1
        end = np.searchsorted(edges, hi, side="left")
        start = max(0, min(bins - 1, start))
        end = max(start + 1, min(bins, end))
        hist[start:end] += vol / (end - start)

    total = hist.sum()
    if total <= 0:
        return {}

    poc_idx = int(np.argmax(hist))
    included = {poc_idx}
    covered = hist[poc_idx]
    lo_i = hi_i = poc_idx
    while covered < total * value_area and (lo_i > 0 or hi_i < bins - 1):
        below = hist[lo_i - 1] if lo_i > 0 else -1.0
        above = hist[hi_i + 1] if hi_i < bins - 1 else -1.0
        if above >= below:
            hi_i += 1
            included.add(hi_i)
            covered += max(above, 0.0)
        else:
            lo_i -= 1
            included.add(lo_i)
            covered += max(below, 0.0)

    mean, std = hist.mean(), hist.std() or 1.0
    hvn = [round(float(centers[i]), 6) for i in np.where(hist > mean + std)[0]]
    lvn = [round(float(centers[i]), 6) for i in np.where(hist < mean - 0.5 * std)[0]]

    return {
        "poc": round(float(centers[poc_idx]), 6),
        "vah": round(float(centers[max(included)]), 6),
        "val": round(float(centers[min(included)]), 6),
        "high": round(high, 6),
        "low": round(low, 6),
        "bins": [{"price": round(float(c), 6), "volume": round(float(v), 2)}
                 for c, v in zip(centers, hist)],
        "hvn": hvn[:6],
        "lvn": lvn[:6],
        "total_volume": round(float(total), 2),
    }


def pivot_points(prev_high: float, prev_low: float, prev_close: float,
                 method: str = "classic") -> Dict[str, float]:
    if method == "fibonacci":
        p = (prev_high + prev_low + prev_close) / 3.0
        rng = prev_high - prev_low
        return {
            "p": p,
            "r1": p + 0.382 * rng, "r2": p + 0.618 * rng, "r3": p + 1.0 * rng,
            "s1": p - 0.382 * rng, "s2": p - 0.618 * rng, "s3": p - 1.0 * rng,
        }
    p = (prev_high + prev_low + prev_close) / 3.0
    return {
        "p": p,
        "r1": 2 * p - prev_low,
        "s1": 2 * p - prev_high,
        "r2": p + (prev_high - prev_low),
        "s2": p - (prev_high - prev_low),
        "r3": prev_high + 2 * (p - prev_low),
        "s3": prev_low - 2 * (prev_high - p),
    }


# ── divergence ────────────────────────────────────────────────

def find_divergence(price: pd.Series, oscillator: pd.Series,
                    lookback: int = 60, pivot: int = 5) -> Optional[str]:
    """Regular + hidden divergence between price and an oscillator."""
    if len(price) < lookback + pivot * 2:
        return None
    p = price.iloc[-lookback:].to_numpy()
    o = oscillator.iloc[-lookback:].to_numpy()

    def pivots(arr: np.ndarray, high: bool) -> List[int]:
        out = []
        for i in range(pivot, len(arr) - pivot):
            window = arr[i - pivot:i + pivot + 1]
            if (high and arr[i] == window.max()) or (not high and arr[i] == window.min()):
                out.append(i)
        return out

    highs, lows = pivots(p, True), pivots(p, False)
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        if p[b] > p[a] and o[b] < o[a]:
            return "bearish_regular"
        if p[b] < p[a] and o[b] > o[a]:
            return "bearish_hidden"
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        if p[b] < p[a] and o[b] > o[a]:
            return "bullish_regular"
        if p[b] > p[a] and o[b] < o[a]:
            return "bullish_hidden"
    return None


# ── full computation ──────────────────────────────────────────

def compute_all(df: pd.DataFrame) -> Dict[str, Any]:
    """Every indicator series, keyed for reuse by structure/pattern/backtest."""
    close = df["close"]
    out: Dict[str, Any] = {
        "ema20": ema(close, 20), "ema50": ema(close, 50),
        "ema100": ema(close, 100), "ema200": ema(close, 200),
        "ema9": ema(close, 9), "ema21": ema(close, 21),
        "sma20": sma(close, 20), "sma50": sma(close, 50), "sma200": sma(close, 200),
        "atr": atr(df, 14),
        "rsi": rsi(close, 14),
        "macd": macd(close),
        "adx": adx(df, 14),
        "stoch_rsi": stoch_rsi(close),
        "bollinger": bollinger(close),
        "supertrend": supertrend(df),
        "ichimoku": ichimoku(df),
        "obv": obv(df),
        "mfi": mfi(df, 14),
        "cci": cci(df, 20),
        "roc": roc(close, 10),
        "williams_r": williams_r(df, 14),
        "vwap": vwap(df),
    }
    return out


def _last(series: pd.Series, default: Optional[float] = None) -> Optional[float]:
    if series is None or len(series) == 0:
        return default
    value = series.iloc[-1]
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return default
    return float(value)


def snapshot(df: pd.DataFrame, instrument: Instrument,
             computed: Optional[Dict[str, Any]] = None,
             daily_df: Optional[pd.DataFrame] = None) -> Tuple[IndicatorSnapshot, Dict[str, Any]]:
    """Latest indicator values plus the interpretation layer the AI reads."""
    ind = computed or compute_all(df)
    close = float(df["close"].iloc[-1])
    snap = IndicatorSnapshot()

    snap.ema = {k: round(v, instrument.digits) for k, v in {
        "9": _last(ind["ema9"]), "20": _last(ind["ema20"]), "21": _last(ind["ema21"]),
        "50": _last(ind["ema50"]), "100": _last(ind["ema100"]), "200": _last(ind["ema200"]),
    }.items() if v is not None}
    snap.sma = {k: round(v, instrument.digits) for k, v in {
        "20": _last(ind["sma20"]), "50": _last(ind["sma50"]), "200": _last(ind["sma200"]),
    }.items() if v is not None}

    vw = ind["vwap"]
    snap.vwap = _last(vw["vwap"])
    snap.vwap_bands = {k: round(v, instrument.digits) for k, v in {
        "upper1": _last(vw["upper1"]), "lower1": _last(vw["lower1"]),
        "upper2": _last(vw["upper2"]), "lower2": _last(vw["lower2"]),
    }.items() if v is not None}

    atr_val = _last(ind["atr"])
    snap.atr = atr_val
    if atr_val:
        snap.atr_pips = round(instrument.to_pips(atr_val), 1)
        snap.atr_pct = round(atr_val / close * 100.0, 3)

    snap.rsi = round(_last(ind["rsi"], 50.0), 2)
    snap.rsi_divergence = find_divergence(df["close"], ind["rsi"])

    macd_df = ind["macd"]
    snap.macd = {
        "line": round(_last(macd_df["line"], 0.0), 6),
        "signal": round(_last(macd_df["signal"], 0.0), 6),
        "hist": round(_last(macd_df["hist"], 0.0), 6),
    }
    hist = macd_df["hist"]
    prev_hist = float(hist.iloc[-2]) if len(hist) > 1 and pd.notna(hist.iloc[-2]) else 0.0
    if snap.macd["hist"] > 0:
        snap.macd_state = "bullish_expanding" if snap.macd["hist"] > prev_hist else "bullish_fading"
    else:
        snap.macd_state = "bearish_expanding" if snap.macd["hist"] < prev_hist else "bearish_fading"

    adx_df = ind["adx"]
    snap.adx = round(_last(adx_df["adx"], 0.0), 2)
    snap.di_plus = round(_last(adx_df["plus_di"], 0.0), 2)
    snap.di_minus = round(_last(adx_df["minus_di"], 0.0), 2)
    snap.trend_strength = ("strong" if (snap.adx or 0) >= 25
                           else "moderate" if (snap.adx or 0) >= 18 else "weak")

    sr = ind["stoch_rsi"]
    snap.stoch_rsi = {"k": round(_last(sr["k"], 50.0), 2), "d": round(_last(sr["d"], 50.0), 2)}

    ich = ind["ichimoku"]
    snap.ichimoku = {k: round(v, instrument.digits) for k, v in {
        "tenkan": _last(ich["tenkan"]), "kijun": _last(ich["kijun"]),
        "senkou_a": _last(ich["senkou_a"]), "senkou_b": _last(ich["senkou_b"]),
    }.items() if v is not None}
    span_a, span_b = snap.ichimoku.get("senkou_a"), snap.ichimoku.get("senkou_b")
    if span_a is not None and span_b is not None:
        cloud_top, cloud_bottom = max(span_a, span_b), min(span_a, span_b)
        if close > cloud_top:
            snap.ichimoku_state = "above_cloud"
        elif close < cloud_bottom:
            snap.ichimoku_state = "below_cloud"
        else:
            snap.ichimoku_state = "in_cloud"

    st = ind["supertrend"]
    snap.supertrend = _last(st["supertrend"])
    snap.supertrend_dir = int(st["direction"].iloc[-1]) if len(st) else 0

    bb = ind["bollinger"]
    snap.bollinger = {k: round(v, 6) for k, v in {
        "upper": _last(bb["upper"]), "mid": _last(bb["mid"]), "lower": _last(bb["lower"]),
        "width": _last(bb["width"]), "pctb": _last(bb["pctb"]),
    }.items() if v is not None}
    width_series = bb["width"].dropna()
    if len(width_series) > 60:
        pct_rank = float((width_series.iloc[-1] <= width_series.iloc[-120:]).mean())
        snap.bb_squeeze = pct_rank > 0.80
        snap.volatility_regime = ("compressed" if pct_rank > 0.75
                                  else "expanded" if pct_rank < 0.2 else "normal")

    obv_series = ind["obv"]
    snap.obv = _last(obv_series)
    if len(obv_series) > 20:
        recent = obv_series.iloc[-20:]
        span = float(recent.max() - recent.min()) or 1.0
        snap.obv_slope = round(float(recent.iloc[-1] - recent.iloc[0]) / span, 3)

    snap.mfi = round(_last(ind["mfi"], 50.0), 2)
    snap.cci = round(_last(ind["cci"], 0.0), 2)
    snap.roc = round(_last(ind["roc"], 0.0), 3)
    snap.williams_r = round(_last(ind["williams_r"], -50.0), 2)

    # Pivots from the previous completed daily bar when available.
    pivot_src = daily_df if daily_df is not None and len(daily_df) >= 2 else df
    if len(pivot_src) >= 2:
        prev = pivot_src.iloc[-2]
        snap.pivots = {k: round(v, instrument.digits) for k, v in
                       pivot_points(float(prev["high"]), float(prev["low"]),
                                    float(prev["close"])).items()}

    snap.volume_profile = volume_profile(df.tail(240))

    vol = df["volume"]
    if len(vol) > 20 and vol.tail(20).mean() > 0:
        snap.relative_volume = round(float(vol.iloc[-1] / vol.tail(20).mean()), 2)

    return snap, ind
