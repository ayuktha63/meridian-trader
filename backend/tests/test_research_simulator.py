"""The research simulator: each rule of evidence on hand-written candles,
then the whole thing held to the engine's own backtest where they overlap."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app import costs
from app.backtest import engine as bt
from app.backtest.replay import BarSignal, Setup
from app.instruments import get_instrument
from app.research.simulator import LIMIT, STOP, Bars, Signal, simulate
from app.schemas.journal import BacktestRequest

OTC = get_instrument("OTCUSD")
HOUR = 3600
PERP = "test_perp"
FLAT = (100, 101, 99.5, 100)


def frame(rows, begin=1_700_000_000.0) -> pd.DataFrame:
    data = np.array(rows, dtype=float)
    return pd.DataFrame({
        "time": begin + np.arange(len(rows)) * float(HOUR), "open": data[:, 0],
        "high": data[:, 1], "low": data[:, 2], "close": data[:, 3],
        "volume": np.full(len(rows), 1000.0)})


def run(rows, signals, **kwargs):
    params = dict(profile=PERP, include_costs=False)
    params.update(kwargs)
    return simulate(Bars.from_frame(frame(rows), "1h"), signals, OTC, **params)


def long(stop=95.0, **kwargs):
    return Signal(direction=1, stop=stop, **kwargs)


def short(stop=105.0, **kwargs):
    return Signal(direction=-1, stop=stop, **kwargs)


# ── entries ───────────────────────────────────────────────────

def test_a_market_order_fills_at_the_next_open_never_at_the_close_that_decided_it():
    trade, = run([FLAT, (101, 103, 100.5, 102), (102, 104, 101, 103)], {0: long()})
    assert (trade.signal_index, trade.entry_index, trade.entry) == (0, 1, 101.0)
    assert trade.risk == 5.0, "planned at the close of 100, against a stop at 95"
    assert trade.reason == "end"
    assert trade.gross_r == pytest.approx((103 - 101) / 5.0)


def test_a_market_order_that_would_open_beyond_its_stop_is_not_sent():
    assert run([FLAT, (94, 96, 93, 95), FLAT], {0: long()}) == []


def test_a_resting_limit_needs_the_market_to_trade_through_it():
    signals = {0: long(entry=LIMIT, price=99.0, stop=94.0)}
    assert run([FLAT, (100, 101, 99.0, 100), FLAT], signals) == [], "touched, not through"
    inside, = run([FLAT, (100, 101, 98.5, 100), FLAT], signals)
    assert inside.entry == 99.0
    gapped, = run([FLAT, (98, 99, 97.5, 98.5), FLAT], signals)
    assert gapped.entry == 98.0, "opened through it: better than asked"


def test_a_resting_entry_expires():
    signals = {0: long(entry=LIMIT, price=99.0, stop=94.0, valid_bars=2)}
    rows = [FLAT, (100, 101, 99.5, 100), (100, 101, 99.5, 100), (100, 101, 98, 99), FLAT]
    assert run(rows, signals) == [], "it would have filled on the third bar; it was gone"
    signals = {0: long(entry=LIMIT, price=99.0, stop=94.0, valid_bars=3)}
    assert len(run(rows, signals)) == 1


def test_an_entry_stop_triggers_on_a_touch_and_pays_for_a_gap():
    signals = {0: long(entry=STOP, price=102.0, stop=97.0)}
    touched, = run([FLAT, (100, 102.0, 99.5, 101.5), FLAT], signals)
    assert touched.entry == 102.0
    gapped, = run([FLAT, (103.5, 104, 103, 103.8), FLAT], signals)
    assert gapped.entry == 103.5, "opened past the trigger: filled there, not at 102"
    assert run([FLAT, (100, 101.9, 99.5, 101), FLAT], signals) == []


# ── exits ─────────────────────────────────────────────────────

def test_the_stop_triggers_on_a_touch_and_fills_at_the_open_on_a_gap():
    touched, = run([FLAT, (100, 101, 95.0, 96), FLAT], {0: long()})
    assert (touched.reason, touched.exit, touched.gross_r) == ("stop", 95.0, -1.0)
    gapped, = run([FLAT, (100, 101, 99, 100), (92, 93, 91, 92), FLAT], {0: long()})
    assert (gapped.reason, gapped.exit) == ("gap", 92.0)
    assert gapped.gross_r == pytest.approx(-1.6), "worse than the 1R that was planned"


def test_stop_and_target_in_one_bar_is_a_loss():
    trade, = run([FLAT, (100, 111, 94, 105), FLAT], {0: long(target=110.0)})
    assert (trade.reason, trade.gross_r) == ("stop", -1.0)


def test_a_target_needs_the_market_to_trade_through_it():
    waiting, = run([FLAT, (100, 110.0, 99, 109), (109, 109.5, 108, 109)], {0: long(target=110.0)})
    assert waiting.reason == "end"
    taken, = run([FLAT, (100, 110.5, 99, 109), FLAT], {0: long(target=110.0)})
    assert (taken.reason, taken.exit, taken.gross_r) == ("target", 110.0, 2.0)


def test_a_target_on_the_bar_a_limit_filled_inside_is_not_credited():
    signals = {0: long(entry=LIMIT, price=99.0, stop=94.0, target=109.0)}
    trade, = run([FLAT, (100, 112, 98, 108), (108, 109.5, 107, 108), FLAT], signals)
    assert (trade.entry_index, trade.exit_index, trade.reason) == (1, 2, "target")


def test_a_resting_order_filled_beyond_its_stop_is_taken_out_where_it_opened():
    signals = {0: long(entry=LIMIT, price=99.0, stop=94.0)}
    trade, = run([FLAT, (92, 93, 91, 92), FLAT], signals)
    assert (trade.entry, trade.exit, trade.reason, trade.gross_r) == (92.0, 92.0, "gap", 0.0)


def test_a_trailing_stop_follows_the_best_price_and_only_ever_tightens():
    rows = [FLAT] * 20 + [(100, 106, 100, 105), (105, 112, 104, 111), (111, 113, 110, 112),
                          (112, 112.5, 103, 104), FLAT]
    trade, = run(rows, {19: long(stop=95.0, trail_atr=2.0)})
    assert trade.reason == "trail"
    assert 105.0 < trade.exit < 113.0, "trailed up behind 113, well above the 95 it began at"
    assert trade.gross_r > 1.0
    assert trade.mfe_r == pytest.approx((113 - 100) / 5.0)


def test_a_time_stop_leaves_at_the_close():
    rows = [FLAT, (100, 101, 99, 100.5), (100.5, 101, 100, 100.8), (100.8, 102, 100, 101.5), FLAT]
    trade, = run(rows, {0: long(max_hold=2)})
    assert (trade.reason, trade.bars_held, trade.exit) == ("time", 2, 101.5)


def test_a_signal_exit_acts_at_the_next_open():
    rows = [FLAT, (100, 101, 99, 100.5), (100.5, 103, 100, 102.5), (102, 103, 101, 102), FLAT]
    leave = [False, False, True, False, False]          # decided at the close of bar 2
    trade, = run(rows, {0: long()}, exit_long=leave)
    assert (trade.reason, trade.exit_index, trade.exit) == ("signal", 3, 102.0)


def test_shorts_mirror_longs_and_are_not_taken_where_they_cannot_be():
    rows = [FLAT, (100, 100.5, 89.5, 91), FLAT]
    trade, = run(rows, {0: short(target=90.0)})
    assert (trade.direction, trade.reason, trade.gross_r) == (-1, "target", 2.0)
    assert run(rows, {0: short(target=90.0)}, profile="test_spot") == []


def test_one_position_at_a_time():
    rows = [FLAT] * 6
    trades = run(rows, {0: long(), 1: long(), 2: long()})
    assert len(trades) == 1


def test_a_signal_with_its_stop_on_the_wrong_side_is_ignored():
    assert run([FLAT, FLAT, FLAT], {0: long(stop=105.0)}) == []
    assert run([FLAT, FLAT, FLAT], {0: long(target=99.0)}) == []
    assert run([FLAT, FLAT, FLAT], {0: Signal(direction=0, stop=95.0)}) == []


def test_the_worst_point_of_a_trade_is_recorded():
    rows = [FLAT, (100, 101, 96, 100), (100, 108, 99, 107), FLAT]
    trade, = run(rows, {0: long(max_hold=2)})
    assert trade.mae_r == pytest.approx(-0.8), "it was 4 of its 5 points under water"


# ── costs ─────────────────────────────────────────────────────

def test_every_trade_is_charged_and_the_charge_follows_the_venue_and_the_fill():
    rows = [FLAT, (100, 110.5, 99, 109), FLAT]
    free, = run(rows, {0: long(target=110.0)})
    perp, = run(rows, {0: long(target=110.0)}, include_costs=True)
    spot, = run(rows, {0: long(target=110.0)}, include_costs=True, profile="test_spot")
    doubled, = run(rows, {0: long(target=110.0)}, include_costs=True, cost_multiplier=2.0)
    assert free.cost_r == 0.0 and free.r == free.gross_r == 2.0
    assert 0 < perp.cost_r < spot.cost_r
    assert perp.r == pytest.approx(perp.gross_r - perp.cost_r)
    assert doubled.cost_r == pytest.approx(2 * perp.cost_r)
    expected = costs.round_trip_cost(OTC, 100.0, 110.0, exit_passive=True, profile=PERP)
    assert perp.cost_r == pytest.approx(expected / 5.0)

    stopped, = run([FLAT, (100, 101, 94, 95), FLAT], {0: long()}, include_costs=True)
    assert stopped.cost_r > perp.cost_r * 0.9, "a stop crosses the spread and slips"


def test_time_in_the_market_is_financed_on_a_perpetual():
    rows = [FLAT] + [(100, 101, 99, 100)] * 30 + [FLAT]
    quick, = run(rows, {0: long(max_hold=1)}, include_costs=True)
    slow, = run(rows, {0: long(max_hold=24)}, include_costs=True)
    assert slow.cost_r - quick.cost_r == pytest.approx(
        costs.carry_cost(OTC, 100.0, 23 * HOUR, PERP) / 5.0)


# ── against the engine's own backtest ─────────────────────────

def test_it_agrees_with_the_engine_backtest_trade_for_trade():
    """Where the two overlap — a limit at the signal price with a fixed stop
    and target, no ladder — they are independent implementations of the same
    rules and must produce the same trades. A random market, a setup every
    few bars, both directions, costs on."""
    rng = np.random.default_rng(21)
    n = 2500
    close = 30_000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.006, n)))
    opens = np.concatenate([[30_000.0], close[:-1]]) * (1 + rng.normal(0, 0.0015, n))
    wick = np.abs(rng.normal(0.0, 0.004, n)) * close
    data = pd.DataFrame({
        "time": 1_700_000_000.0 + np.arange(n) * float(HOUR), "open": opens,
        "high": np.maximum(opens, close) + wick, "low": np.minimum(opens, close) - wick,
        "close": close, "volume": np.full(n, 10.0)})

    engine_signals, research_signals = {}, {}
    for i in range(30, n - 1, 3):
        direction = 1 if rng.random() < 0.5 else -1
        entry = float(close[i]) * (1 - direction * 0.002)
        stop = entry * (1 - direction * rng.uniform(0.006, 0.02))
        target = entry * (1 + direction * rng.uniform(0.008, 0.04))
        engine_signals[i] = BarSignal(engine=Setup(
            "BUY" if direction > 0 else "SELL", entry, stop, target, 70.0, "x"))
        research_signals[i] = Signal(direction=direction, stop=stop, target=target,
                                     entry=LIMIT, price=entry, valid_bars=1,
                                     max_hold=bt.MAX_HOLD_BARS)

    request = BacktestRequest(symbol="OTCUSD", timeframe="1h", strategy="engine",
                              min_confidence=0.0, risk_reward=0.0, use_ladder=False,
                              cost_profile=PERP)
    prep = bt.prepare(data, OTC, "1h", engine_signals, PERP)
    theirs, _ = bt._simulate(prep, OTC, request, 0, n)      # noqa: SLF001
    theirs = [t for t in theirs if t.outcome != "open"]
    ours = [t for t in simulate(Bars.from_frame(data, "1h"), research_signals, OTC,
                                profile=PERP) if t.reason != "end"]

    assert len(theirs) == len(ours) > 150
    for a, b in zip(theirs, ours):
        assert (a.index, a.bars_held) == (b.entry_index, b.bars_held)
        assert a.exit_price == pytest.approx(b.exit, abs=0.011)   # theirs is rounded to cents
        assert a.r_multiple == pytest.approx(b.r, abs=0.002)
        assert a.cost_r == pytest.approx(b.cost_r, abs=0.002)
