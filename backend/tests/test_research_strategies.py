"""The candidate strategies: each does what its rule says, none of them
knows the future, and the audit catches one that does."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.instruments import get_instrument
from app.research import strategies
from app.research.audit import audit
from app.research.simulator import Bars, Signal, simulate
from app.research.strategies import BY_NAME, CANDIDATES, Plan, Strategy

BTC = get_instrument("BTCUSD")
HOUR = 3600


def market(n=3000, seed=5, drift=0.0, vol=0.006, start=30_000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    opens = np.concatenate([[start], close[:-1]])
    wick = np.abs(rng.normal(0.0, vol / 2, n)) * close
    return pd.DataFrame({
        "time": 1_600_000_000.0 + np.arange(n) * float(HOUR), "open": opens,
        "high": np.maximum(opens, close) + wick, "low": np.minimum(opens, close) - wick,
        "close": close, "volume": rng.lognormal(3, 0.3, n)})


def test_the_grid_is_declared_up_front_and_counted():
    assert [s.name for s in CANDIDATES] == [
        "donchian_trend", "ts_momentum", "range_reversion", "trend_pullback", "vol_expansion"]
    assert [len(s.grid) for s in CANDIDATES] == [9, 8, 8, 4, 4]
    assert strategies.trials() == 33
    for strategy in CANDIDATES:
        assert len({strategies.label(p) for p in strategy.grid}) == len(strategy.grid)
        for text in (strategy.hypothesis, strategy.works_when, strategy.fails_when,
                     strategy.falsified_by, strategy.entry_rule, strategy.exit_rule):
            assert len(text) > 30, "every candidate says what it believes and what would refute it"


@pytest.mark.parametrize("strategy", CANDIDATES, ids=lambda s: s.name)
def test_every_signal_is_a_tradable_plan(strategy):
    frame = market()
    for params in strategy.grid:
        plan = strategy.build(frame, params)
        close = frame["close"].to_numpy()
        assert plan.signals, "{} {} never signalled on 3,000 bars".format(
            strategy.name, strategies.label(params))
        for i, signal in plan.signals.items():
            assert i >= strategy.warmup(params) - 1
            assert (close[i] - signal.stop) * signal.direction > 0, "stop on the losing side"
            if signal.target is not None:
                assert (signal.target - close[i]) * signal.direction > 0


@pytest.mark.parametrize("strategy", CANDIDATES, ids=lambda s: s.name)
def test_no_candidate_sees_the_future_or_needs_more_history_than_it_says(strategy):
    frame = market(n=2600, seed=9)
    for params in (strategy.grid[0], strategy.grid[-1]):
        report = audit(strategy, frame, params, samples=16)
        assert report.checked >= 16
        assert report.passed, report.to_dict()


def test_donchian_enters_on_a_new_extreme_and_stops_k_atrs_away():
    frame = market()
    plan = BY_NAME["donchian_trend"].build(frame, {"n": 20.0, "k": 3.0})
    i, signal = next((i, s) for i, s in sorted(plan.signals.items()) if s.direction > 0)
    assert frame["close"][i] > frame["high"][i - 20:i].max()
    assert signal.trail_atr == 3.0 and signal.target is None
    longs = [i for i, s in plan.signals.items() if s.direction > 0]
    assert all(frame["close"][i] > frame["high"][i - 20:i].max() for i in longs)


def test_momentum_enters_on_the_turn_and_leaves_when_it_turns_back():
    frame = market()
    plan = BY_NAME["ts_momentum"].build(frame, {"lookback": 24.0, "k": 3.0})
    close = frame["close"].to_numpy()
    for i, signal in plan.signals.items():
        now, before = close[i] / close[i - 24] - 1, close[i - 1] / close[i - 25] - 1
        assert (now > 0 and before <= 0) if signal.direction > 0 else (now < 0 and before >= 0)
    i = next(i for i, s in sorted(plan.signals.items()) if s.direction > 0)
    assert plan.exit_long[i] is False
    assert plan.exit_long[i - 1] is True, "the bar before, the return was not yet positive"


def test_reversion_fades_a_close_outside_the_band_towards_its_middle():
    frame = market()
    plan = BY_NAME["range_reversion"].build(frame, {"mult": 2.0, "k": 1.5, "adx_max": 25.0})
    mid = frame["close"].rolling(20).mean()
    for i, signal in plan.signals.items():
        assert signal.target == pytest.approx(mid[i])
        assert signal.max_hold == 24
        assert (frame["close"][i] < mid[i]) == (signal.direction > 0)


def test_a_strategy_that_reads_the_next_bar_is_caught():
    """The classic leak: a shifted index. Its backtest is spectacular and
    describes nothing that could be traded."""
    def peek(df: pd.DataFrame, params) -> Plan:
        close = df["close"].to_numpy()
        tomorrow = df["close"].shift(-1).to_numpy()
        plan = Plan()
        for i in range(120, len(df)):
            if tomorrow[i] > close[i] * 1.002:          # NaN on the last bar: no signal
                plan.signals[i] = Signal(1, close[i] * 0.97, max_hold=1)
        return plan

    cheat = Strategy("peek", "h" * 40, "w" * 40, "f" * 40, "x" * 40, "e" * 40, "e" * 40,
                     ({},), peek, lambda p: 120)
    frame = market(n=1500)
    report = audit(cheat, frame, {})
    assert not report.passed
    assert report.lookahead and "removed" in report.lookahead[0]
    trades = simulate(Bars.from_frame(frame, "1h"), peek(frame, {}).signals, BTC,
                      profile="crypto_perp")
    assert np.mean([t.r for t in trades]) > 0.1, "and it looks wonderful, which is the danger"


def test_a_strategy_that_normalises_over_the_whole_history_is_caught():
    """Subtler: nothing is shifted, but a statistic taken over all the data
    includes the future of every bar but the last."""
    def zscore(df: pd.DataFrame, params) -> Plan:
        close = df["close"]
        z = ((close - close.mean()) / close.std()).to_numpy()
        plan = Plan()
        for i in range(120, len(df) - 1):
            if z[i] < -1.0:
                plan.signals[i] = Signal(1, float(close[i]) * 0.97, max_hold=24)
        return plan

    cheat = Strategy("zscore", "h" * 40, "w" * 40, "f" * 40, "x" * 40, "e" * 40, "e" * 40,
                     ({},), zscore, lambda p: 120)
    assert not audit(cheat, market(n=1500, drift=0.0004), {}, samples=40).passed


def test_a_strategy_that_needs_more_history_than_it_admits_is_caught():
    def slow(df: pd.DataFrame, params) -> Plan:
        close = df["close"]
        average = close.ewm(span=600, adjust=False).mean().to_numpy()
        plan = Plan()
        for i in range(50, len(df)):
            if close[i] > average[i]:
                plan.signals[i] = Signal(1, float(average[i]) * 0.95, max_hold=5)
        return plan

    liar = Strategy("slow", "h" * 40, "w" * 40, "f" * 40, "x" * 40, "e" * 40, "e" * 40,
                    ({},), slow, lambda p: 50)
    report = audit(liar, market(n=2500, drift=0.0003), {})
    assert not report.lookahead, "it is causal"
    assert report.warmup, "but fifty bars is not what it was computed on"
