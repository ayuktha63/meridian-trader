"""The intraday candidates: no look-ahead, no trade across a session, the
rules do what they say, and a protocol cannot move after it is frozen."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from india_fixtures import intraday_rows, weekdays

from app.research import india_costing, india_study, india_strategies as S
from app.research.audit import audit
from app.research.simulator import Bars, simulate
from app.data.store import COLUMNS


def frame(days: int = 60, seed: int = 3, start: date = date(2026, 1, 5)) -> pd.DataFrame:
    rows = intraday_rows(weekdays(start, days), 15, seed=seed)
    return pd.DataFrame(rows, columns=list(COLUMNS))


def bars_with(day_rows):
    return pd.DataFrame(day_rows, columns=list(COLUMNS))


def test_the_clock_facts_each_bar_carries():
    df = frame(3)
    day = S.Day(df)
    assert list(day.pos[:3]) == [0, 1, 2] and day.pos[25] == 0 and day.pos[24] == 24
    assert day.sod[0] == 9 * 3600 + 900
    # the 14:45 bar can be entered... its exit bar (15:00's) closes at 15:15, past the line
    first_day = np.flatnonzero(day.id == 0)
    last_holdable = [i for i in first_day if day.hold[i] >= 0][-1]
    assert day.pos[last_holdable] == 21, "decision at 14:30's close at the latest"
    assert day.can_enter(0) and not day.can_enter(int(first_day[-1]))


def test_previous_day_values_never_include_today():
    df = frame(4)
    day = S.Day(df)
    d0 = df[day.id == 0]
    assert np.isnan(day.prev_close[0])
    assert day.prev_close[26] == d0["close"].iloc[-1]
    assert day.prev_high[26] == d0["high"].max() and day.prev_low[26] == d0["low"].min()


@pytest.mark.parametrize("strategy", S.CANDIDATES, ids=lambda s: s.name)
def test_every_candidate_passes_the_look_ahead_and_warmup_audit(strategy):
    df = frame(120, seed=11)
    for params in (strategy.grid[0], strategy.grid[-1]):
        report = audit(strategy, df, params, samples=16)
        assert report.passed, (strategy.name, params, report.lookahead[:2], report.warmup[:2])


@pytest.mark.parametrize("strategy", S.CANDIDATES, ids=lambda s: s.name)
def test_no_trade_is_held_past_the_session_or_entered_late(strategy):
    df = frame(120, seed=5)
    day = S.Day(df)
    bars = Bars.from_frame(df, "15m")
    from app.research.india_study import _instrument
    for params in strategy.grid:
        plan = strategy.build(df, params)
        for i in plan.signals:
            assert day.sod[i] <= S.LAST_ENTRY and day.hold[i] >= 0
        for trade in simulate(bars, plan.signals, _instrument("X"), include_costs=False):
            assert india_study._same_day(trade), (strategy.name, params)


def test_an_opening_range_break_is_taken_once_and_stops_at_the_far_side():
    df = frame(4, seed=2)
    day = S.Day(df)
    d = np.flatnonzero(day.id == 2)
    f = df.copy()
    base = float(df.loc[d[0], "open"])
    unit = float(S._atr(df)[d[2]])                     # the scale this market moves on
    half = 0.6 * unit
    # day 2: a tight opening range (2 bars), then a clean break up on the third
    for i in d[:2]:
        f.loc[i, ["open", "high", "low", "close"]] = [base, base + half, base - half, base]
    f.loc[d[2], ["open", "high", "low", "close"]] = [base, base + 2 * half, base - 0.1, base + 1.8 * half]
    f.loc[d[3], ["open", "high", "low", "close"]] = [base + 1.8 * half, base + 3 * half,
                                                     base + half, base + 2.5 * half]
    plan = S._orb(f, {"or_bars": 2.0, "rr": 2.0})
    mine = {i: s for i, s in plan.signals.items() if day.id[i] == 2}
    assert list(mine) == [int(d[2])], "the first break, and only that"
    sig = mine[int(d[2])]
    close = base + 1.8 * half
    assert sig.direction == 1 and sig.stop == pytest.approx(base - half)
    assert sig.target == pytest.approx(close + 2.0 * (close - (base - half)))


def test_a_gap_reversal_targets_yesterdays_close():
    df = frame(3, seed=4)
    day = S.Day(df)
    d1, d2 = np.flatnonzero(day.id == 1), np.flatnonzero(day.id == 2)
    f = df.copy()
    prev = f.loc[d1[-1], "close"]
    top = prev * 1.012
    f.loc[d2[0], ["open", "high", "low", "close"]] = [top, top * 1.001, top * 0.996, top * 0.997]
    plan = S._gap_reversal(f, {"gap": 0.004, "decide": 0.0})
    sig = plan.signals.get(int(d2[0]))
    assert sig is not None and sig.direction == -1 and sig.target == pytest.approx(prev)


def test_declared_grids_are_counted_and_unbuilt_hypotheses_are_listed():
    assert S.trials() == sum(len(s.grid) for s in S.CANDIDATES) == 40
    assert set(S.NOT_BUILT) == {"cross_sectional_momentum", "daily_mean_reversion"}
    assert {s.name for s in S.CANDIDATES} >= {"orb15", "trend15", "trend15_vol", "gap_trend15"}
    for s in S.CANDIDATES:
        assert s.hypothesis and s.falsified_by and s.entry_rule and s.exit_rule


def test_trades_are_costed_order_by_order_and_a_small_account_trades_nothing():
    df = frame(200, seed=9, start=date(2024, 10, 14))      # inside the research stretch
    mkt = india_study.make_market("TEST", "x", df)
    strategy = S.CHANNEL
    params = strategy.grid[0]
    big = india_study.run_config([mkt], strategy, params, "research",
                                 india_costing.Account(1_000_000.0, 0.005,
                                                       india_study.ACCOUNT.product))
    tiny = india_study.run_config([mkt], strategy, params, "research",
                                  india_costing.Account(293.44, 0.01, india_study.ACCOUNT.product))
    assert len(tiny.costed) == 0 and tiny.skipped >= 0
    for c in big.costed:
        assert c.cost.charges.brokerage >= 10.0 - 1e-9, "two orders, each at least Rs 5"
        assert c.net == pytest.approx(c.gross - c.cost.costs)


def test_the_frozen_protocol_cannot_be_moved(tmp_path, monkeypatch):
    monkeypatch.setattr(india_study, "FOLDER", tmp_path)
    monkeypatch.setattr(india_study, "PROTOCOL_PATH", tmp_path / "protocol.json")
    with pytest.raises(RuntimeError, match="No protocol is frozen"):
        india_study.frozen()
    record = india_study.freeze()
    assert india_study.frozen()["sha256"] == record["sha256"]
    with pytest.raises(RuntimeError, match="already frozen"):
        india_study.freeze()
    # a grid edited after the fact no longer matches
    original = S.CANDIDATES
    from dataclasses import replace
    monkeypatch.setattr(S, "CANDIDATES", (replace(S.ORB, grid=S.ORB.grid[:2]),) + original[1:])
    with pytest.raises(RuntimeError, match="no longer matches"):
        india_study.frozen()
    monkeypatch.setattr(S, "CANDIDATES", original)
    path = tmp_path / "protocol.json"
    path.write_text(path.read_text().replace('"trials": 40', '"trials": 3'))
    with pytest.raises(RuntimeError, match="edited"):
        india_study.frozen()


def test_the_study_thresholds_are_the_existing_ones():
    from app.research import study
    body = india_study.protocol()
    assert body["thresholds"] == study.THRESHOLDS
    assert body["thresholds"]["research"]["min_trades"] == 200
    assert body["thresholds"]["research"]["deflated_sharpe"] == 0.95
    assert body["universe"]["survivorship_free"] is False


def test_the_random_baseline_is_matched_and_runs():
    df = frame(160, seed=21, start=date(2024, 10, 14))
    mkt = india_study.make_market("TEST", "x", df)
    strategy, params = S.CHANNEL, S.CHANNEL.grid[0]
    result = india_study.run_config(
        [mkt], strategy, params, "research",
        india_costing.Account(1_000_000.0, 0.005, india_study.ACCOUNT.product))
    assert len(result.costed) > 20
    null = india_study.random_baseline([mkt], strategy, result, draws=5)
    assert null.shape == (5,) and np.isfinite(null).all()
    assert not np.allclose(null, null[0]), "different draws differ"
