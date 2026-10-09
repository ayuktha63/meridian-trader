"""Does the backtest flatter? Each test here is a way it used to, or could."""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import costs
from app.backtest import edge_verdict, walk_forward
from app.backtest.engine import WARMUP, _simulate
from app.config import settings
from app.instruments import get_instrument
from app.main import app
from app.schemas.journal import BacktestRequest, BacktestTrade

RELIANCE = get_instrument("RELIANCE")


def random_walk(n=3000, seed=11, start=2500.0, vol=0.004) -> pd.DataFrame:
    """A market with no edge in it at all: independent steps, zero drift."""
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(0.0, vol, n)))
    opens = np.concatenate([[start], close[:-1]])
    wick = np.abs(rng.normal(0.0, vol / 2, n)) * close
    return pd.DataFrame({
        "time": 1_700_000_000.0 + np.arange(n) * 3600.0, "open": opens,
        "high": np.maximum(opens, close) + wick, "low": np.minimum(opens, close) - wick,
        "close": close, "volume": rng.lognormal(10, 0.3, n)})


def closed(trades):
    return [t for t in trades if t.outcome != "open"]


def expectancy(trades):
    return float(np.mean([t.r_multiple for t in closed(trades)]))


def test_random_entries_lose_money_after_costs():
    """The control. Coin-flip entries have zero edge, so net of costs they
    must come out negative — on every seed, not on average."""
    request = BacktestRequest(symbol="RELIANCE", strategy="random", risk_reward=2.0)
    for seed in (1, 2, 3, 4, 5):
        df = random_walk(seed=seed)
        trades, _ = _simulate(df, RELIANCE, request.model_copy(update={"seed": seed}),
                              WARMUP, len(df))
        assert len(closed(trades)) > 60
        assert expectancy(trades) < 0, "seed {} flattered a coin flip".format(seed)


def test_costs_are_charged_on_every_trade_and_only_make_it_worse():
    df = random_walk()
    request = BacktestRequest(symbol="RELIANCE", strategy="random")
    net, _ = _simulate(df, RELIANCE, request, WARMUP, len(df))
    gross, _ = _simulate(df, RELIANCE, request.model_copy(update={"include_costs": False}),
                         WARMUP, len(df))
    assert len(net) == len(gross)
    assert all(t.cost_r > 0 for t in closed(net))
    assert all(t.cost_r == 0 for t in closed(gross))
    for with_cost, without in zip(closed(net), closed(gross)):
        assert with_cost.r_multiple < without.r_multiple
    # Without costs a coin flip is roughly break-even; that gap is the point.
    assert abs(expectancy(gross)) < 0.25


def test_the_cost_model_is_the_one_paper_trading_uses():
    from app.services import paper

    assert paper.COMMISSION_RATE is costs.COMMISSION_RATE
    one_way = costs.round_trip_cost(RELIANCE, 2500.0, 2500.0)
    assert one_way > RELIANCE.from_pips(RELIANCE.typical_spread_pips), "more than the spread"
    assert costs.round_trip_cost(RELIANCE, 2500.0, 2480.0, stopped=True) > \
        costs.round_trip_cost(RELIANCE, 2500.0, 2480.0)
    # Indian segments carry statutory charges on top of brokerage; crypto does not.
    assert costs.per_side_rate(RELIANCE) > costs.per_side_rate(get_instrument("BTCUSD"))


def test_nothing_from_the_future_reaches_a_decision():
    """Replay a prefix of the series, then the whole series. Every trade that
    was complete inside the prefix must be identical in both — if any later
    bar influenced it, it is not."""
    df = random_walk(n=1600, seed=3)
    compared = 0
    for strategy in ("confluence", "structure", "mean_reversion", "breakout"):
        request = BacktestRequest(symbol="RELIANCE", strategy=strategy, min_confidence=40.0)
        cut = 1100
        short, _ = _simulate(df.iloc[:cut].reset_index(drop=True), RELIANCE, request, WARMUP, cut)
        full, _ = _simulate(df, RELIANCE, request, WARMUP, len(df))
        done = [t for t in short if t.outcome != "open"]
        compared += len(done)
        for a, b in zip(done, full):
            assert a.model_dump() == b.model_dump(), strategy
    assert compared > 40, "the comparison has to actually compare something"


def test_a_signal_fills_at_the_next_bars_open_not_its_own_close():
    df = random_walk(n=900, seed=5)
    trades, _ = _simulate(df, RELIANCE, BacktestRequest(symbol="RELIANCE", strategy="random"),
                          WARMUP, len(df))
    opens = df["open"].to_numpy()
    assert trades
    for trade in trades:
        assert trade.entry == pytest.approx(opens[trade.index], abs=0.01)


def _trades(rs):
    return [BacktestTrade(index=i, time=i, direction="BUY", entry=1, stop_loss=0.9,
                          take_profit=1.2, outcome="win" if r > 0 else "loss",
                          r_multiple=r) for i, r in enumerate(rs)]


def test_an_edge_needs_enough_trades_and_an_interval_clear_of_zero():
    rng = np.random.default_rng(0)
    assert edge_verdict([], "holdout").passed is False

    few = edge_verdict(_trades([2.0] * 10), "holdout")
    assert few.passed is False
    assert "too few" in few.reason

    # +0.05R on average with 1R of noise: positive, and indistinguishable from luck.
    noisy = edge_verdict(_trades(rng.normal(0.05, 1.0, 60)), "holdout")
    assert noisy.passed is False
    assert "includes zero" in noisy.reason

    losing = edge_verdict(_trades(rng.normal(-0.2, 1.0, 200)), "holdout")
    assert losing.passed is False
    assert losing.ci_high < 0.1

    real = edge_verdict(_trades(rng.normal(0.4, 1.0, 200)), "walk_forward")
    assert real.passed is True
    assert real.ci_low > 0
    assert real.method == "walk_forward"


def test_walk_forward_finds_no_edge_in_a_random_walk(monkeypatch):
    """Nine parameter sets, each picked for looking best on its training
    stretch — and on noise, none of it survives the stretch that follows."""
    from app.backtest import engine

    df = random_walk(n=3000, seed=21)

    async def frame(request):
        return df
    monkeypatch.setattr(engine, "_frame", frame)
    result = asyncio.run(walk_forward(BacktestRequest(symbol="RELIANCE", timeframe="1h")))
    assert len(result.folds) == 4
    assert result.edge.method == "walk_forward"
    assert result.edge.passed is False
    assert any(f["train_expectancy"] > 0 for f in result.folds), "fitting always finds something"


# ── the go-live gate ──────────────────────────────────────────

def test_live_orders_are_refused_without_a_measured_edge(monkeypatch):
    from app.backtest import engine
    from app.broker.angelone import angel_auth, angel_instruments
    from app.broker.trading_mode import PAPER, trading_mode
    from fake_angel import FakeAngel
    from test_execution import (
        FAKE_MATCH, _fake_connect, _run, _seed_recommendation, _wipe_executions,
    )

    async def resolve(symbol):
        return FAKE_MATCH
    monkeypatch.setattr(angel_instruments, "resolve", resolve)
    monkeypatch.setattr(settings, "live_require_measured_edge", True)
    fake = FakeAngel().install(monkeypatch)
    _run(_wipe_executions())
    _fake_connect()
    trading_mode.set_mode("LIVE", confirmed=True)
    try:
        with TestClient(app) as client:
            def execute():
                rec = _run(_seed_recommendation(suggested_lots=1.0))
                return client.post(f"/api/trades/{rec}/execute", json={"confirmed": True}).json()

            body = execute()
            assert body["status"] == "REJECTED"
            assert "No measured edge" in body["error_message"]
            assert client.get("/api/backtest/edge?symbol=RELIANCE&timeframe=1h").json()[
                "passed"] is False

            # A walk-forward that finds nothing does not open the gate either.
            df = random_walk(n=3000, seed=21)

            async def frame(request):
                return df
            monkeypatch.setattr(engine, "_frame", frame)
            ran = client.post("/api/backtest/walk-forward",
                              json={"symbol": "RELIANCE", "timeframe": "1h"}).json()
            assert ran["edge"]["passed"] is False
            assert "No measured edge" in execute()["error_message"]
            assert fake.placed == []

            # Only a passing verdict does.
            from app.schemas.journal import EdgeVerdict

            def passing(trades, method, seed=7):
                return EdgeVerdict(passed=True, reason="measured", method=method, trades=80)
            monkeypatch.setattr(engine, "edge_verdict", passing)
            client.post("/api/backtest/walk-forward",
                        json={"symbol": "RELIANCE", "timeframe": "1h"})
            assert execute()["status"] == "OPEN"
            assert len(fake.placed) == 1
    finally:
        trading_mode.set_mode(PAPER)
        angel_auth._session = None  # noqa: SLF001
