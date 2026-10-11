from __future__ import annotations

import pytest

from app.analysis import indicators, plan as plan_builder, structure as structure_engine
from app.backtest import run_backtest
from app.schemas.analysis import Decision
from app.schemas.journal import BacktestRequest


def _snapshot(df, instrument):
    snap, _ = indicators.snapshot(df, instrument)
    return snap


def test_buy_plan_has_stop_below_entry(eurusd, uptrend):
    snap = _snapshot(uptrend, eurusd)
    struct = structure_engine.analyse(uptrend, eurusd, snap.atr)
    price = float(uptrend["close"].iloc[-1])
    plan = plan_builder.build(Decision.BUY, price, snap, struct, eurusd)

    assert plan.entry is not None and plan.stop_loss is not None
    assert plan.stop_loss < plan.entry
    assert plan.take_profit is None or plan.take_profit > plan.entry
    assert plan.risk_pips and plan.risk_pips > 0
    assert plan.suggested_lots and plan.suggested_lots > 0


def test_sell_plan_has_stop_above_entry(eurusd, uptrend):
    snap = _snapshot(uptrend, eurusd)
    struct = structure_engine.analyse(uptrend, eurusd, snap.atr)
    price = float(uptrend["close"].iloc[-1])
    plan = plan_builder.build(Decision.SELL, price, snap, struct, eurusd)

    assert plan.stop_loss > plan.entry
    assert plan.take_profit is None or plan.take_profit < plan.entry


def test_stop_distance_respects_volatility_bounds(eurusd, uptrend):
    snap = _snapshot(uptrend, eurusd)
    struct = structure_engine.analyse(uptrend, eurusd, snap.atr)
    price = float(uptrend["close"].iloc[-1])
    plan = plan_builder.build(Decision.BUY, price, snap, struct, eurusd)

    risk = abs(plan.entry - plan.stop_loss)
    assert risk >= snap.atr * (plan_builder.MIN_STOP_ATR * 0.9)
    assert risk <= snap.atr * (plan_builder.MAX_STOP_ATR + plan_builder.STOP_BUFFER_ATR)


def test_position_size_matches_risk_budget(eurusd, uptrend):
    snap = _snapshot(uptrend, eurusd)
    struct = structure_engine.analyse(uptrend, eurusd, snap.atr)
    price = float(uptrend["close"].iloc[-1])
    plan = plan_builder.build(Decision.BUY, price, snap, struct, eurusd,
                              balance=10_000.0, risk_pct=1.0)

    # lots * pip_value * stop_pips should recover the intended risk amount.
    implied = plan.suggested_lots * eurusd.pip_value_per_lot * plan.risk_pips
    assert abs(implied - 100.0) < 5.0


def test_wait_plan_sizes_nothing(eurusd, uptrend):
    snap = _snapshot(uptrend, eurusd)
    struct = structure_engine.analyse(uptrend, eurusd, snap.atr)
    plan = plan_builder.build(Decision.WAIT, float(uptrend["close"].iloc[-1]),
                              snap, struct, eurusd)
    assert plan.entry is None
    assert plan.suggested_lots is None
    assert any("No position" in note for note in plan.position_notes)


def test_wide_spread_is_flagged(eurusd, uptrend):
    snap = _snapshot(uptrend, eurusd)
    struct = structure_engine.analyse(uptrend, eurusd, snap.atr)
    plan = plan_builder.build(Decision.BUY, float(uptrend["close"].iloc[-1]),
                              snap, struct, eurusd, spread_pips=25.0)
    assert any("Spread" in note for note in plan.position_notes)


@pytest.mark.asyncio
async def test_backtest_produces_coherent_metrics():
    # Each bar is a full run of the live engine, so the window is kept small.
    result = await run_backtest(BacktestRequest(
        symbol="EURUSD", timeframe="1h", bars=160, min_confidence=45, risk_reward=1.5))

    metrics = result.metrics
    assert result.strategy == "engine"
    assert result.bars_tested == 160
    assert metrics.wins + metrics.losses <= metrics.trades
    assert 0.0 <= metrics.win_rate <= 100.0
    assert metrics.max_drawdown >= 0.0
    assert metrics.profit_factor >= 0.0
    assert result.notes
    assert result.in_sample is not None
    assert result.out_of_sample is not None
    assert result.in_sample.trades + result.out_of_sample.trades == metrics.trades

    for trade in result.trades:
        assert trade.direction in ("BUY", "SELL")
        # Stop and target are the plan's; the entry is where the limit filled,
        # which a gap can put anywhere at or better than the plan's price.
        if trade.direction == "BUY":
            assert trade.stop_loss < trade.take_profit
            assert trade.entry < trade.take_profit
        else:
            assert trade.take_profit < trade.stop_loss
            assert trade.take_profit < trade.entry
        if trade.outcome != "open":
            assert trade.exit_reason in ("stop", "target", "eod", "timeout")
            assert trade.exit_time is not None
            # A fill at the bar's open can be stopped within that same bar.
            assert trade.exit_time >= trade.time
            assert trade.bars_held >= 0
            assert trade.cost_r > 0


@pytest.mark.asyncio
async def test_a_second_strategy_on_the_same_market_reuses_the_replay():
    """One pass of the engine yields its own verdict and every named
    strategy; running another of them must not pay for the pass again."""
    import time

    request = BacktestRequest(symbol="EURUSD", timeframe="1h", bars=160,
                              min_confidence=45, risk_reward=1.0)
    await run_backtest(request)
    started = time.perf_counter()
    for strategy in ("trend_continuation", "mean_reversion", "breakout",
                     "smart_money", "momentum"):
        result = await run_backtest(request.model_copy(update={"strategy": strategy}))
        assert result.strategy == strategy
        assert result.bars_tested == 160
    assert time.perf_counter() - started < 3.0


@pytest.mark.asyncio
async def test_backtest_rejects_insufficient_history(monkeypatch):
    from app.backtest import engine as backtest_engine
    from conftest import trending_df

    async def short(request):
        return trending_df(n=120)
    monkeypatch.setattr(backtest_engine, "_frame", short)
    result = await run_backtest(BacktestRequest(symbol="EURUSD", timeframe="1w"))
    assert result.metrics.trades == 0
    assert any("history" in note.lower() for note in result.notes)
