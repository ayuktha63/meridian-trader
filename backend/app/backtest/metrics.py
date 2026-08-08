"""Performance metrics.

Sharpe and Sortino are computed **per trade**, not annualised, because trade
frequency varies with the strategy and timeframe — annualising a 40-trade
sample would manufacture precision that is not there. Read them as
risk-adjusted quality of the R distribution.
"""
from __future__ import annotations

import math
from typing import Dict, List

import numpy as np

from ..schemas.journal import BacktestMetrics, BacktestTrade


def compute(trades: List[BacktestTrade], starting_balance: float,
            equity_curve: List[Dict[str, float]], bars_tested: int) -> BacktestMetrics:
    closed = [t for t in trades if t.outcome in ("win", "loss", "breakeven")]
    metrics = BacktestMetrics(trades=len(closed), ending_balance=starting_balance)
    if not closed:
        return metrics

    r_values = np.array([t.r_multiple for t in closed], dtype=float)
    pnl_values = np.array([t.pnl for t in closed], dtype=float)

    wins = r_values[r_values > 0]
    losses = r_values[r_values < 0]

    metrics.wins = int(len(wins))
    metrics.losses = int(len(losses))
    metrics.win_rate = round(float(len(wins) / len(closed) * 100), 2)

    gross_profit = float(pnl_values[pnl_values > 0].sum())
    gross_loss = abs(float(pnl_values[pnl_values < 0].sum()))
    metrics.profit_factor = round(gross_profit / gross_loss, 3) if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else 0.0)

    metrics.average_r = round(float(r_values.mean()), 3)
    metrics.total_r = round(float(r_values.sum()), 2)
    metrics.largest_win_r = round(float(wins.max()), 2) if len(wins) else 0.0
    metrics.largest_loss_r = round(float(losses.min()), 2) if len(losses) else 0.0

    win_rate = len(wins) / len(closed)
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = abs(float(losses.mean())) if len(losses) else 0.0
    metrics.expectancy = round(win_rate * avg_win - (1 - win_rate) * avg_loss, 3)

    std = float(r_values.std(ddof=1)) if len(r_values) > 1 else 0.0
    metrics.sharpe_ratio = round(float(r_values.mean() / std), 3) if std > 0 else 0.0
    downside = r_values[r_values < 0]
    d_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    metrics.sortino_ratio = round(float(r_values.mean() / d_std), 3) if d_std > 0 else 0.0

    # Streaks
    best = worst = current = 0
    for r in r_values:
        if r > 0:
            current = current + 1 if current > 0 else 1
            best = max(best, current)
        elif r < 0:
            current = current - 1 if current < 0 else -1
            worst = min(worst, current)
    metrics.longest_win_streak = int(best)
    metrics.longest_loss_streak = int(abs(worst))

    # Drawdown from the equity curve
    if equity_curve:
        equity = np.array([point["equity"] for point in equity_curve], dtype=float)
        running_max = np.maximum.accumulate(equity)
        drawdown = running_max - equity
        peak = float(drawdown.max())
        metrics.max_drawdown = round(peak, 2)
        idx = int(np.argmax(drawdown))
        base = float(running_max[idx]) or starting_balance
        metrics.max_drawdown_pct = round(peak / base * 100, 2) if base else 0.0
        metrics.ending_balance = round(float(equity[-1]), 2)
        metrics.return_pct = round(
            (metrics.ending_balance - starting_balance) / starting_balance * 100, 2)

    if bars_tested > 0:
        bars_in_market = sum(t.bars_held for t in closed)
        metrics.exposure_pct = round(bars_in_market / bars_tested * 100, 2)

    if math.isinf(metrics.profit_factor):
        metrics.profit_factor = 999.0
    return metrics
