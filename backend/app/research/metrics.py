"""What a list of trades amounts to.

Everything is in R — the risk planned at the signal — and net of costs
unless it says gross. Returns in percent assume a fixed fraction of the
starting balance risked per trade, not compounded: compounding makes a good
curve look better and a bad one look survivable, and neither is information
about the strategy.

A figure the sample is too small to support is not reported as a number
and then qualified in a footnote. It is left out, and `unreliable` says why.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from . import stats
from .simulator import Trade

#: Below these, an annualised figure is arithmetic on noise.
MIN_TRADES_FOR_RATIOS = 30
MIN_DAYS_FOR_ANNUAL = 180
DAY = 86_400.0


def _drawdown(curve: np.ndarray, times: np.ndarray) -> Dict[str, float]:
    """Deepest fall from a peak, in the curve's units, and the longest time
    spent below one."""
    if not len(curve):
        return {"depth": 0.0, "days": 0.0}
    peak = np.maximum.accumulate(np.r_[0.0, curve])[1:]
    depth = float((peak - curve).max())
    longest = since = 0.0
    last_peak_time = times[0]
    for value, high, moment in zip(curve, peak, times):
        if value >= high:
            last_peak_time = moment
        since = moment - last_peak_time
        longest = max(longest, since)
    return {"depth": depth, "days": longest / DAY}


def _side(trades: Sequence[Trade], direction: int) -> Dict[str, Any]:
    r = np.array([t.r for t in trades if t.direction == direction], dtype=float)
    return {"trades": int(len(r)), "expectancy_r": round(float(r.mean()), 4) if len(r) else None,
            "win_rate": round(float((r > 0).mean()), 4) if len(r) else None}


def _by_year(trades: Sequence[Trade]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, List[float]] = {}
    for trade in trades:
        out.setdefault(time.strftime("%Y", time.gmtime(trade.exit_time)), []).append(trade.r)
    return {year: {"trades": len(values), "total_r": round(float(np.sum(values)), 2),
                   "expectancy_r": round(float(np.mean(values)), 4)}
            for year, values in sorted(out.items())}


def buy_and_hold(close: Sequence[float]) -> Dict[str, float]:
    """The benchmark that takes no skill: buy the first bar, hold to the last."""
    prices = np.asarray(close, dtype=float)
    if len(prices) < 2:
        return {"return_pct": 0.0, "max_drawdown_pct": 0.0}
    peak = np.maximum.accumulate(prices)
    return {"return_pct": round(float(prices[-1] / prices[0] - 1.0) * 100, 2),
            "max_drawdown_pct": round(float(((peak - prices) / peak).max()) * 100, 2)}


def summarise(trades: Sequence[Trade], *, window_seconds: float, tf_seconds: int,
              risk_fraction: float = 0.01) -> Dict[str, Any]:
    ordered = sorted(trades, key=lambda t: t.exit_time)
    out: Dict[str, Any] = {"trades": len(ordered), "unreliable": []}
    if not ordered:
        out["unreliable"].append("no trades")
        return out

    r = np.array([t.r for t in ordered], dtype=float)
    gross = np.array([t.gross_r for t in ordered], dtype=float)
    cost = np.array([t.cost_r for t in ordered], dtype=float)
    exits = np.array([t.exit_time for t in ordered], dtype=float)
    wins, losses = r[r > 0], r[r < 0]
    years = window_seconds / (365.25 * DAY)

    out.update({
        "expectancy_r": round(float(r.mean()), 4),
        "gross_expectancy_r": round(float(gross.mean()), 4),
        "cost_r_per_trade": round(float(cost.mean()), 4),
        "total_r": round(float(r.sum()), 2),
        "gross_total_r": round(float(gross.sum()), 2),
        "net_return_pct": round(float(r.sum()) * risk_fraction * 100, 2),
        "gross_return_pct": round(float(gross.sum()) * risk_fraction * 100, 2),
        #: Per trade, on an account risking `risk_fraction` of 10,000.
        "expectancy_currency": round(float(r.mean()) * risk_fraction * 10_000, 2),
        "win_rate": round(float((r > 0).mean()), 4),
        "average_win_r": round(float(wins.mean()), 4) if len(wins) else 0.0,
        "average_loss_r": round(float(losses.mean()), 4) if len(losses) else 0.0,
        "profit_factor": round(float(wins.sum() / -losses.sum()), 3) if len(losses) else None,
        "worst_trade_r": round(float(r.min()), 3),
        "best_trade_r": round(float(r.max()), 3),
        "tail_5pct_r": round(float(np.quantile(r, 0.05)), 3),
        "average_adverse_excursion_r": round(float(np.mean([t.mae_r for t in ordered])), 3),
        "average_holding_bars": round(float(np.mean([t.bars_held for t in ordered])), 1),
        "average_holding_hours": round(
            float(np.mean([t.bars_held for t in ordered])) * tf_seconds / 3600.0, 1),
        "exposure_pct": round(min(sum(t.bars_held for t in ordered) * tf_seconds
                                  / max(window_seconds, 1.0), 1.0) * 100, 2),
        "trades_per_year": round(len(ordered) / years, 1) if years > 0 else None,
        "long": _side(ordered, 1), "short": _side(ordered, -1),
        "exits": {reason: int(sum(1 for t in ordered if t.reason == reason))
                  for reason in sorted({t.reason for t in ordered})},
        "by_year": _by_year(ordered),
    })
    positive_gross = float(gross[gross > 0].sum())
    out["costs_pct_of_gross_profit"] = (
        round(float(cost.sum()) / positive_gross * 100, 1) if positive_gross > 0 else None)

    fall = _drawdown(np.cumsum(r), exits)
    out["max_drawdown_r"] = round(fall["depth"], 2)
    out["max_drawdown_pct"] = round(fall["depth"] * risk_fraction * 100, 2)
    out["longest_drawdown_days"] = round(fall["days"], 1)

    out["sharpe_per_trade"] = out["sortino_per_trade"] = None
    out["annualised_return_pct"] = out["annualised_volatility_pct"] = None
    out["annualised_sharpe"] = out["calmar"] = None
    if len(r) < MIN_TRADES_FOR_RATIOS:
        out["unreliable"].append(
            "{} trades: ratios need {}".format(len(r), MIN_TRADES_FOR_RATIOS))
        return out
    out["sharpe_per_trade"] = round(stats.sharpe(r), 4)
    downside = float(np.sqrt(np.mean(np.minimum(r, 0.0) ** 2)))
    out["sortino_per_trade"] = round(float(r.mean()) / downside, 4) if downside > 0 else None

    days = window_seconds / DAY
    if days < MIN_DAYS_FOR_ANNUAL:
        out["unreliable"].append(
            "{:.0f} days: annual figures need {}".format(days, MIN_DAYS_FOR_ANNUAL))
        return out
    # Daily profit and loss as a fraction of the starting balance: the R
    # closed that day, zero on the days nothing closed.
    daily = np.zeros(int(days) + 1)
    first = exits[0] - (exits[0] % DAY)
    index = np.clip(((exits - first) // DAY).astype(int), 0, len(daily) - 1)
    np.add.at(daily, index, r * risk_fraction)
    annual_return = float(daily.sum()) / years
    annual_vol = float(daily.std(ddof=1)) * np.sqrt(365.25)
    out["annualised_return_pct"] = round(annual_return * 100, 2)
    out["annualised_volatility_pct"] = round(annual_vol * 100, 2)
    out["annualised_sharpe"] = round(annual_return / annual_vol, 3) if annual_vol > 0 else None
    deepest = fall["depth"] * risk_fraction
    out["calmar"] = round(annual_return / deepest, 3) if deepest > 0 else None
    return out


def describe_series(values: Sequence[float]) -> Optional[Dict[str, float]]:
    x = np.asarray(values, dtype=float)
    if not len(x):
        return None
    return {"n": int(len(x)), "mean": round(float(x.mean()), 4),
            "median": round(float(np.median(x)), 4), "min": round(float(x.min()), 4),
            "max": round(float(x.max()), 4)}
