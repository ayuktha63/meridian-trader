"""Does the backtest flatter? Each test here is a way it used to, or could."""
from __future__ import annotations

import asyncio
import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import costs
from app.backtest import edge_verdict, run_backtest, walk_forward
from app.backtest import engine as bt
from app.backtest import replay
from app.backtest.replay import BarSignal, FrameBuilder, Setup
from app.config import settings
from app.db.models import BacktestRow, ExperimentRow, PaperTradeRow, StrategyApprovalRow
from app.db.session import session_scope
from app.instruments import get_instrument
from app.main import app
from app.schemas.journal import BacktestRequest, BacktestTrade
from app.research import approvals, registry, versions
from app.services.edge import holm_survivors, latest_edge

RELIANCE = get_instrument("RELIANCE")
OTC = get_instrument("OTCUSD")
HOUR = 3600


def run(coro):
    return asyncio.run(coro)


def random_walk(n=3000, seed=11, start=2500.0, vol=0.004, step=HOUR,
                begin=1_700_000_000.0) -> pd.DataFrame:
    """A market with no edge in it at all: independent steps, zero drift."""
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(0.0, vol, n)))
    opens = np.concatenate([[start], close[:-1]])
    wick = np.abs(rng.normal(0.0, vol / 2, n)) * close
    return pd.DataFrame({
        "time": begin + np.arange(n) * float(step), "open": opens,
        "high": np.maximum(opens, close) + wick, "low": np.minimum(opens, close) - wick,
        "close": close, "volume": rng.lognormal(10, 0.3, n)})


def bars(rows, step=HOUR, begin=1_700_000_000.0) -> pd.DataFrame:
    """Hand-written candles: (open, high, low, close)."""
    data = np.array(rows, dtype=float)
    return pd.DataFrame({
        "time": begin + np.arange(len(rows)) * float(step), "open": data[:, 0],
        "high": data[:, 1], "low": data[:, 2], "close": data[:, 3],
        "volume": np.full(len(rows), 1000.0)})


def closed(trades):
    return [t for t in trades if t.outcome != "open"]


def expectancy(trades):
    return float(np.mean([t.r_multiple for t in closed(trades)]))


def trade(frame, signals, instrument=OTC, timeframe="1h", **request):
    """Run the order lifecycle over hand-written candles and signals."""
    params = dict(symbol=instrument.symbol, timeframe=timeframe, strategy="engine",
                  min_confidence=0.0, risk_reward=0.0, include_costs=False,
                  use_ladder=False, cost_profile="test_perp")
    params.update(request)
    prep = bt.prepare(frame, instrument, timeframe, signals, params["cost_profile"])
    trades, _ = bt._simulate(prep, instrument, BacktestRequest(**params), 0, len(frame))
    return trades


def buy(entry=100.0, stop=95.0, target=110.0, confidence=70.0):
    return BarSignal(engine=Setup("BUY", entry, stop, target, confidence, "test"))


def sell(entry=100.0, stop=105.0, target=90.0, confidence=70.0):
    return BarSignal(engine=Setup("SELL", entry, stop, target, confidence, "test"))


FLAT = (100, 101, 99.5, 100)           # a quiet bar around 100


# ── the order's life ──────────────────────────────────────────

def test_a_limit_entry_is_missed_when_the_market_does_not_come_back():
    """Signal at 100; the next bar opens above and never trades down to it.
    A backtest that fills that order anyway is crediting trades that ran
    away — exactly the ones a limit order does not get."""
    frame = bars([FLAT, (101, 104, 100.5, 103), (103, 112, 102, 111)])
    assert trade(frame, {0: buy()}) == []


def test_a_limit_entry_fills_at_its_price_or_better():
    inside = trade(bars([FLAT, (101, 102, 99.5, 101), FLAT]), {0: buy()})
    assert [t.entry for t in inside] == [100.0], "traded down to the limit"

    gapped = trade(bars([FLAT, (98, 99, 97.5, 98.5), FLAT]), {0: buy()})
    assert [t.entry for t in gapped] == [98.0], "opened through it: filled at the open"

    short = trade(bars([FLAT, (99, 100.5, 98, 99), FLAT]), {0: sell()})
    assert [t.entry for t in short] == [100.0]


def test_a_bar_that_only_touches_a_resting_order_does_not_fill_it():
    """Reaching the price is not being filled at it: an order resting there
    is behind everyone who got there first. The market has to trade through."""
    touched = bars([FLAT, (101, 102, 100.0, 101), FLAT])
    assert trade(touched, {0: buy()}) == []
    assert trade(bars([FLAT, (99, 100.0, 98, 99), FLAT]), {0: sell()}) == []

    # The same for the target, which is a resting order too.
    held = bars([FLAT, (100, 100.5, 99.8, 100), (100, 110.0, 99.9, 109), (109, 109.5, 108, 109)])
    still_open, = trade(held, {0: buy()})
    assert still_open.outcome == "open", "110 was printed, not traded through"
    through = bars([FLAT, (100, 100.5, 99.8, 100), (100, 110.5, 99.9, 109), FLAT])
    done, = trade(through, {0: buy()})
    assert (done.exit_reason, done.exit_price) == ("target", 110.0)


def test_a_stop_triggers_on_a_touch():
    """The other way round for a stop: it becomes a market order the moment
    its price prints, and gets no benefit of the doubt."""
    frame = bars([FLAT, (100, 100.5, 99.8, 100), (100, 101, 95.0, 96), FLAT])
    done, = trade(frame, {0: buy()})
    assert (done.exit_reason, done.exit_price) == ("stop", 95.0)


def test_a_signal_cannot_fill_on_the_bar_that_produced_it():
    frame = bars([(100, 112, 94, 100), (101, 102, 100.5, 101.5)])
    assert trade(frame, {0: buy()}) == [], "bar 0's own range is in the past"


def test_stop_and_target_in_one_bar_is_a_loss():
    frame = bars([FLAT, (100, 100.5, 99.8, 100), (100, 111, 94, 105), FLAT])
    done, = trade(frame, {0: buy()})
    assert (done.exit_reason, done.exit_price, done.r_multiple) == ("stop", 95.0, -1.0)


def test_a_gap_through_the_stop_fills_at_the_open_not_at_the_stop():
    frame = bars([FLAT, (100, 100.5, 99.8, 100), (92, 93, 91, 92.5), FLAT])
    done, = trade(frame, {0: buy()})
    assert (done.exit_reason, done.exit_price) == ("stop", 92.0)
    assert done.r_multiple == pytest.approx(-1.6), "worse than the 1R that was planned"


def test_a_target_on_the_bar_the_limit_filled_inside_is_not_credited():
    """The bar traded 101 -> 99.5 -> 111. Whether 111 came before or after
    the fill at 100 is unknowable from a candle, so it is not counted."""
    frame = bars([FLAT, (101, 111, 99.5, 108), (108, 109, 107, 108), (108, 112, 107, 111)])
    done, = trade(frame, {0: buy()})
    assert done.index == 1
    assert (done.exit_reason, done.exit_time) == ("target", int(frame["time"][3]))


def test_a_stop_on_the_bar_the_limit_filled_inside_is_charged():
    frame = bars([FLAT, (101, 102, 94, 96), FLAT])
    done, = trade(frame, {0: buy()})
    assert (done.exit_reason, done.bars_held, done.r_multiple) == ("stop", 0, -1.0)


def test_only_setups_that_clear_the_thresholds_are_taken():
    frame = bars([FLAT, (100, 100.5, 99.8, 100), (100, 111, 99, 110), FLAT])
    signals = {0: buy(confidence=60.0)}                 # reward:risk is 2.0
    assert len(trade(frame, signals, min_confidence=60.0, risk_reward=2.0)) == 1
    assert trade(frame, signals, min_confidence=61.0) == []
    assert trade(frame, signals, risk_reward=2.1) == []


def test_one_position_at_a_time_and_none_while_an_order_rests():
    frame = bars([FLAT, (100, 100.5, 99.8, 100)] + [FLAT] * 4)
    taken = trade(frame, {0: buy(), 1: buy(), 2: buy(), 3: buy()})
    assert len(taken) == 1


def test_r_is_measured_against_the_risk_that_was_sized():
    """Filled at 98 on a gap, stopped at 95: 3 points lost on an order sized
    for 5. Dividing by the 3 would call it a full 1R loss."""
    frame = bars([FLAT, (98, 98.5, 97.5, 98), (98, 98.2, 94, 95), FLAT])
    done, = trade(frame, {0: buy()})
    assert done.r_multiple == pytest.approx(-0.6)


def test_the_ladder_turns_a_reversal_into_a_small_win():
    """Up to +1.6R, then all the way back through the entry. With the stop
    left where it started that is a full loss; ratcheted, it is not."""
    path = [FLAT, (100, 100.5, 99.8, 100)]
    path += [(100 + i, 101.2 + i, 99.9 + i, 101 + i) for i in range(8)]     # to 108
    path += [(108, 108.2, 103, 103.5), (103.5, 104, 94, 95), FLAT]
    signals = {0: buy(target=120.0)}

    plain, = trade(bars(path), signals, use_ladder=False)
    assert (plain.exit_reason, plain.r_multiple) == ("stop", -1.0)

    laddered, = trade(bars(path), signals, use_ladder=True)
    assert laddered.exit_reason == "stop"
    assert laddered.exit_price > laddered.entry
    assert laddered.r_multiple > 0


# ── intraday: the session ends, and so does the position ──────

def _sessions(days=3, per_day=6, step=HOUR):
    """Flat hourly bars in `per_day`-bar sessions on consecutive IST days."""
    first = 1_704_080_700.0                    # 2024-01-01 09:15 IST
    times = [first + day * 86_400 + k * step for day in range(days) for k in range(per_day)]
    frame = bars([FLAT] * len(times), step=step)
    frame["time"] = times
    return frame


def test_an_intraday_position_is_squared_off_at_the_session_end():
    frame = _sessions()
    done, = trade(frame, {0: buy(stop=90.0, target=120.0)}, instrument=RELIANCE)
    assert done.index == 1
    assert (done.exit_reason, done.exit_time) == ("eod", int(frame["time"][5]))
    assert done.bars_held == 4, "never carried into the next session"


def test_a_market_that_never_closes_is_not_squared_off():
    done, = trade(_sessions(), {0: buy(stop=90.0, target=120.0)}, instrument=OTC)
    assert done.outcome == "open"


def test_no_entry_is_taken_too_close_to_the_session_end(monkeypatch):
    frame = _sessions()
    wide = dict(stop=90.0, target=120.0)
    # The last bar of the day: the market is closing as the order would go in.
    assert trade(frame, {5: buy(**wide)}, instrument=RELIANCE) == []
    # One hourly bar left is more than the 45-minute window: allowed.
    assert len(trade(frame, {4: buy(**wide)}, instrument=RELIANCE)) == 1
    monkeypatch.setattr(settings, "live_no_entry_minutes_before_close", 90.0)
    assert trade(frame, {4: buy(**wide)}, instrument=RELIANCE) == []
    assert len(trade(frame, {3: buy(**wide)}, instrument=RELIANCE)) == 1


# ── costs ─────────────────────────────────────────────────────

def test_random_entries_lose_money_after_costs():
    """The control. Coin-flip entries have zero edge, so net of costs they
    must come out negative — on every seed, not on average."""
    request = BacktestRequest(symbol="RELIANCE", strategy="random", risk_reward=2.0)
    for seed in (1, 2, 3, 4, 5):
        frame = random_walk(seed=seed, vol=0.002)
        prep = bt.prepare(frame, RELIANCE, "1h")
        trades, _ = bt._simulate(prep, RELIANCE, request.model_copy(update={"seed": seed}),
                                 bt.WARMUP, len(frame))
        assert len(closed(trades)) > 60
        assert expectancy(trades) < 0, "seed {} flattered a coin flip".format(seed)


def test_costs_are_charged_on_every_trade_and_only_make_it_worse():
    frame = random_walk(vol=0.002)
    prep = bt.prepare(frame, RELIANCE, "1h")
    request = BacktestRequest(symbol="RELIANCE", strategy="random")
    net, _ = bt._simulate(prep, RELIANCE, request, bt.WARMUP, len(frame))
    gross, _ = bt._simulate(prep, RELIANCE, request.model_copy(update={"include_costs": False}),
                            bt.WARMUP, len(frame))
    assert len(net) == len(gross)
    assert all(t.cost_r > 0 for t in closed(net))
    assert all(t.cost_r == 0 for t in closed(gross))
    for with_cost, without in zip(closed(net), closed(gross)):
        assert with_cost.r_multiple < without.r_multiple
    # Without costs a coin flip is roughly break-even; that gap is the point.
    assert abs(expectancy(gross)) < 0.25


def test_paper_trading_and_the_backtest_charge_from_one_model():
    from app.services import paper

    assert paper.commission(1_000_000.0, RELIANCE) == pytest.approx(
        1_000_000.0 * costs.per_side_rate(RELIANCE))
    # Indian segments carry statutory charges and GST on top of brokerage.
    assert costs.per_side_rate(RELIANCE) > costs.INDIA_BROKERAGE_RATE * (1 + costs.GST)
    # A market off any exchange is charged by the venue a run names, and on
    # some of those a resting order is cheaper than a taken one.
    assert costs.per_side_rate(OTC, profile="test_spot") == pytest.approx(0.0020 * 1.18)
    assert costs.per_side_rate(OTC, profile="test_perp") == pytest.approx(0.0005 * 1.18)
    assert costs.per_side_rate(OTC, passive=True, profile="test_perp") == pytest.approx(
        0.0002 * 1.18)
    # An equity round trip lands near 0.27% of turnover — 0.1% brokerage a
    # side since November 2025, with GST on it, and the statutory charges. It
    # was 0.1% when brokerage was 0.03%; an order of magnitude off either way
    # still means a rate was mistyped.
    assert 0.0022 < 2 * costs.per_side_rate(RELIANCE) < 0.0032
    assert costs.per_side_rate(RELIANCE) > costs.EQUITY_BROKERAGE_RATE * (1 + costs.GST)
    assert costs.per_side_rate(get_instrument("NIFTY")) < costs.per_side_rate(RELIANCE), (
        "a future is not charged as an equity")


def test_a_resting_order_pays_no_spread_and_a_stop_pays_slippage():
    half = RELIANCE.from_pips(RELIANCE.typical_spread_pips) / 2
    both_cross = costs.round_trip_cost(RELIANCE, 2500.0, 2500.0)
    assert costs.round_trip_cost(RELIANCE, 2500.0, 2500.0, entry_passive=True) == \
        pytest.approx(both_cross - half)
    assert costs.round_trip_cost(RELIANCE, 2500.0, 2500.0, entry_passive=True,
                                 exit_passive=True) == pytest.approx(both_cross - 2 * half)
    assert costs.round_trip_cost(RELIANCE, 2500.0, 2480.0, stopped=True) > \
        costs.round_trip_cost(RELIANCE, 2500.0, 2480.0)


def test_costs_belong_to_a_venue_and_a_result_can_name_it():
    spot = costs.round_trip_cost(OTC, 60_000.0, 60_600.0, profile="test_spot")
    perp = costs.round_trip_cost(OTC, 60_000.0, 60_600.0, profile="test_perp")
    assert spot > 3 * perp, "the same market, a different book"
    assert costs.describe(OTC, "test_perp")["funding_per_8h"] == 0.0001
    assert costs.describe(OTC)["profile"] == "generic", "no schedule on file for it"
    with pytest.raises(KeyError):
        costs.round_trip_cost(OTC, 60_000.0, 60_600.0, profile="made_up")


def test_an_exchange_product_is_charged_by_its_own_segment_whatever_is_named():
    """Everything Angel One trades has one schedule. Naming a venue for it
    changes nothing, and the record says which schedule it was."""
    own = costs.round_trip_cost(RELIANCE, 2500.0, 2550.0)
    assert costs.round_trip_cost(RELIANCE, 2500.0, 2550.0, profile=costs.EXCHANGE) == own
    assert costs.round_trip_cost(RELIANCE, 2500.0, 2550.0, profile="test_perp") == own
    assert costs.describe(RELIANCE)["profile"] == "india:stock"
    assert costs.describe(RELIANCE, costs.EXCHANGE)["profile"] == "india:stock"
    assert costs.describe(OTC, costs.EXCHANGE)["profile"] == "generic"
    assert costs.known(costs.EXCHANGE) == costs.EXCHANGE and costs.known("generic") == "generic"
    with pytest.raises(KeyError):
        costs.known("made_up")
    assert set(costs.PROFILES) - {"test_perp", "test_spot"} == {"generic"}, \
        "no venue's fees are on file but the placeholder"


def test_holding_a_perpetual_costs_financing_and_a_stop_slips_with_price():
    flat = costs.round_trip_cost(OTC, 60_000.0, 60_000.0, profile="test_perp")
    a_day = costs.round_trip_cost(OTC, 60_000.0, 60_000.0, profile="test_perp",
                                  seconds_held=86_400.0)
    assert a_day - flat == pytest.approx(60_000.0 * 0.0001 * 3), "three funding periods"
    assert costs.carry_cost(OTC, 60_000.0, 86_400.0, "test_spot") == 0.0, "spot has no funding"
    assert costs.carry_cost(RELIANCE, 2500.0, 86_400.0) == 0.0
    stopped = costs.round_trip_cost(OTC, 60_000.0, 59_000.0, stopped=True, profile="test_perp")
    plain = costs.round_trip_cost(OTC, 60_000.0, 59_000.0, profile="test_perp")
    assert stopped - plain == pytest.approx(59_000.0 * 0.0002 + OTC.from_pips(0.5))


def test_a_backtest_is_charged_by_the_profile_it_names():
    frame = bars([FLAT, (100, 100.5, 99.8, 100), (100, 110.5, 99.9, 109), FLAT])
    spot, = trade(frame, {0: buy()}, include_costs=True, cost_profile="test_spot")
    perp, = trade(frame, {0: buy()}, include_costs=True, cost_profile="test_perp")
    assert spot.cost_r > perp.cost_r > 0
    assert spot.r_multiple < perp.r_multiple < 2.0


def test_no_short_is_opened_on_a_spot_book():
    """You can only sell what you hold. A backtest that shorts on a spot
    book is measuring trades nobody could have placed."""
    frame = bars([FLAT, (99, 100.5, 98, 99), (99, 99.5, 89, 90), FLAT])
    assert len(trade(frame, {0: sell()}, cost_profile="test_perp")) == 1
    assert trade(frame, {0: sell()}, cost_profile="test_spot") == []
    assert len(trade(frame, {0: sell(entry=2500, stop=2520, target=2460)}, instrument=RELIANCE,
                     cost_profile=None)) <= 1, "an exchange product traded intraday can be"
    assert costs.can_short(RELIANCE) and not costs.can_short(OTC, "test_spot")


# ── the replay is the live engine, with nothing from the future ──

def _same(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    return a.shape == b.shape and np.allclose(a.to_numpy(float), b.to_numpy(float))


def test_higher_timeframes_are_rebuilt_only_from_what_had_happened():
    """Frames built with the whole series in hand must equal frames built
    from a series that ends at the decision bar."""
    frame = random_walk(n=1500, seed=3, step=900)             # 15-minute bars
    whole = FrameBuilder(frame, "15m")
    for i in (1000, 1101, 1234, 1400, 1498):          # 4h needs 60 bars: 960 of these
        cut = FrameBuilder(frame.iloc[: i + 1], "15m")
        full_frames, cut_frames = whole.frames(i), cut.frames(i)
        assert set(full_frames) == set(cut_frames) == {"15m", "1h", "4h"}
        for tf in full_frames:
            assert _same(full_frames[tf], cut_frames[tf]), (i, tf)
            assert full_frames[tf]["close"].iloc[-1] == frame["close"].iloc[i]
        assert _same(whole.daily(i), cut.daily(i))


def test_a_stored_bar_that_knows_its_own_close_is_never_used_as_is():
    """Stored hourly history holds each hour's *final* values. At 10:15 the
    10:00 bar is still forming; using the stored one would be reading the
    10:59 price."""
    frame = random_walk(n=1200, seed=5, step=900, begin=1_699_999_200.0)   # on the hour
    hourly = FrameBuilder(frame, "15m")._resample(HOUR)       # final values, all of it
    stored = {"1h": pd.DataFrame(hourly, columns=list(replay.COLUMNS))}
    builder = FrameBuilder(frame, "15m", stored)
    # 900..902 are mid-hour; 903 and 1103 are the bar that completes its hour,
    # where the stored bar and the rebuilt one are the same bar — once.
    for i in (900, 901, 902, 903, 1003, 1100, 1103):
        view = builder.frames(i)["1h"]
        assert view["close"].iloc[-1] == frame["close"].iloc[i]
        assert view["time"].is_monotonic_increasing
        assert view["time"].is_unique, i
        assert (view["time"].iloc[:-1] + HOUR <= frame["time"].iloc[i] + 900).all()
        assert _same(view, FrameBuilder(frame.iloc[: i + 1], "15m").frames(i)["1h"])


def test_daily_bars_stamped_at_local_midnight_do_not_leak_the_session():
    """A broker stamps a daily bar at 00:00 IST — 18:30 UTC the day before —
    so by UTC arithmetic today's bar looks like yesterday's and complete."""
    frame = random_walk(n=400, seed=9, step=HOUR, begin=1_704_067_200.0)  # 2024-01-01 00:00 UTC
    days = np.arange(-70, 20)
    stored = pd.DataFrame({
        "time": 1_704_067_200.0 - 19_800 + days * 86_400.0, "open": 1.0, "high": 9e9,
        "low": 1.0, "close": 9e9, "volume": 1.0})
    builder = FrameBuilder(frame, "1h", {"1d": stored})
    for i in (10, 30, 200, 399):
        daily = builder.frames(i)["1d"]
        decided_at = frame["time"].iloc[i] + HOUR
        assert (daily["time"].iloc[:-1] + 86_400 <= decided_at).all()
        assert daily["close"].iloc[-1] == frame["close"].iloc[i]
        assert daily["high"].iloc[-1] < 9e9, "the forming day is built from the chart"


def test_the_engines_conclusion_at_a_bar_ignores_every_later_bar():
    """The real engine, run on a prefix and on the whole series: the same
    answer at every bar the prefix contains."""
    frame = random_walk(n=420, seed=3)
    indices = range(330, 379)
    replay.clear_cache()
    whole = replay.signals("WHOLE", "1h", frame, None, RELIANCE, indices)
    prefix = replay.signals("PREFIX", "1h", frame.iloc[:380], None, RELIANCE, indices)
    replay.clear_cache()
    assert set(whole) == set(prefix) == set(indices)
    assert all(whole[i] == prefix[i] for i in indices)
    assert sum(1 for s in whole.values() if s.engine or s.plays) > 5, \
        "the comparison has to actually compare something"


def test_the_replay_reaches_the_same_verdict_as_the_live_engine(monkeypatch):
    """The claim the backtest rests on. Put a market's history in the store,
    ask the live engine — `engine.run`, the function behind the trade panel —
    and ask the replay about the same bar."""
    from app.analysis import engine
    from app.data.feed import feed
    from app.data.store import store

    symbol, timeframe = "ITC", "1m"
    instrument = get_instrument(symbol)
    frame = random_walk(n=1000, seed=17, start=45.0, vol=0.0015, step=60)
    builder = FrameBuilder(frame, timeframe)

    async def no_hydration(*args, **kwargs):
        return None
    monkeypatch.setattr(feed, "ensure_history", no_hydration)
    monkeypatch.setattr(feed, "quotes", {})

    replay.clear_cache()
    compared = directional = 0
    try:
        for i in range(910, 999, 6):
            store.series(symbol, timeframe).replace(frame.iloc[: i + 1].values.tolist())
            for tf, view in builder.frames(i).items():
                if tf != timeframe:
                    store.series(symbol, tf).replace(view.values.tolist())
            live = run(engine.run(symbol, timeframe, include_ai=False))
            mine = replay.signals(symbol, timeframe, frame, None, instrument, [i])[i].engine

            compared += 1
            if live.verdict.decision.value == "WAIT":
                assert mine is None, i
                continue
            directional += 1
            assert mine is not None, i
            assert mine.direction == live.verdict.decision.value
            assert mine.confidence == live.confidence.final_confidence
            assert (mine.entry, mine.stop, mine.target) == (
                live.plan.entry, live.plan.stop_loss, live.plan.take_profit)
    finally:
        for tf in ("1m", "5m", "15m", "1h", "4h", "1d"):
            store.series(symbol, tf).replace([])
        replay.clear_cache()
    assert compared == 15
    assert directional >= 3, "need directional calls for the comparison to mean anything"


def test_the_named_strategies_come_out_of_the_same_pass():
    frame = random_walk(n=330, seed=3)
    replay.clear_cache()
    signals = replay.signals("PLAYS", "1h", frame, None, RELIANCE, range(260, 329))
    replay.clear_cache()
    seen = {key for s in signals.values() for key in s.plays}
    assert seen <= set(bt.PLAYS)
    assert seen, "no named strategy engaged on 69 bars"
    for signal in signals.values():
        for setup in signal.plays.values():
            if setup.direction == "BUY":
                assert setup.stop < setup.entry < setup.target
            else:
                assert setup.target < setup.entry < setup.stop


def test_an_unknown_strategy_is_refused_not_guessed():
    result = run(run_backtest(BacktestRequest(symbol="EURUSD", strategy="confluence")))
    assert result.metrics.trades == 0
    assert "Unknown strategy" in result.notes[0]


# ── is there an edge ──────────────────────────────────────────

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
    assert noisy.p_value > 0.05

    losing = edge_verdict(_trades(rng.normal(-0.2, 1.0, 200)), "holdout")
    assert losing.passed is False
    assert losing.p_value > 0.9

    real = edge_verdict(_trades(rng.normal(0.4, 1.0, 200)), "walk_forward")
    assert real.passed is True
    assert real.ci_low > 0
    assert real.p_value < 0.001
    assert real.method == "walk_forward"


def test_the_bar_rises_with_the_number_of_markets_tested():
    """Test twenty markets and one clears 5% by luck alone."""
    a, b, c = ("A", "1h"), ("B", "1h"), ("C", "1h")
    assert holm_survivors({a: 0.04}) == {a}
    assert holm_survivors({a: 0.04, b: 0.5}) == set(), "0.04 needs 0.025 with two tests"
    assert holm_survivors({a: 0.02, b: 0.03, c: 0.04}) == set()
    assert holm_survivors({a: 0.001, b: 0.02, c: 0.9}) == {a, b}
    assert holm_survivors({}) == set()


def _fake_frame(monkeypatch, frame):
    async def load(request):
        return frame
    monkeypatch.setattr(bt, "_frame", load)


def test_walk_forward_finds_no_edge_in_random_entries(monkeypatch):
    """Nine parameter sets, each picked for looking best on its training
    stretch — and on noise, none of it survives the stretch that follows."""
    _fake_frame(monkeypatch, random_walk(n=3400, seed=21, vol=0.002))
    result = run(walk_forward(BacktestRequest(
        symbol="RELIANCE", timeframe="1h", strategy="random", bars=3000)))
    assert len(result.folds) == 4
    assert result.edge.method == "walk_forward"
    assert result.edge.passed is False
    for fold in result.folds:
        # A stretch is only traded on thresholds that made money just before
        # it. "The least bad" is not a reason to trade.
        if fold["test_trades"]:
            assert fold["train_expectancy"] > 0
            assert fold["min_confidence"] > 0
        else:
            assert fold["train_expectancy"] == 0
    assert set(result.next_parameters) <= {"min_confidence", "min_risk_reward"}


def test_walk_forward_sits_out_when_nothing_worked_before(monkeypatch):
    """Every grid point lost on the training stretch: the next stretch is
    not traded, and there are no thresholds to take live."""
    _fake_frame(monkeypatch, random_walk(n=3400, seed=21, vol=0.002))
    monkeypatch.setattr(bt, "_best_parameters", lambda *args, **kwargs: (None, 0.0))
    result = run(walk_forward(BacktestRequest(
        symbol="RELIANCE", timeframe="1h", strategy="random", bars=3000)))
    assert result.metrics.trades == 0
    assert result.next_parameters == {}
    assert any("no thresholds to trade with" in note for note in result.notes)


def test_walk_forward_only_reports_bars_it_did_not_fit_on(monkeypatch):
    frame = random_walk(n=3400, seed=21, vol=0.002)
    _fake_frame(monkeypatch, frame)
    result = run(walk_forward(BacktestRequest(
        symbol="RELIANCE", timeframe="1h", strategy="random", bars=3000)))
    first_tested = frame["time"].iloc[len(frame) - 3000 + 600]   # one training stretch in
    assert result.from_time == int(first_tested)
    assert all(t.time >= result.from_time for t in result.trades)


def test_too_little_history_is_said_so(monkeypatch):
    _fake_frame(monkeypatch, random_walk(n=500))
    result = run(walk_forward(BacktestRequest(symbol="RELIANCE", strategy="random")))
    assert result.edge.passed is False
    assert "Not enough history" in result.notes[0]


# ── the go-live evidence ──────────────────────────────────────

async def _wipe_evidence():
    async with session_scope() as session:
        for table in (BacktestRow, PaperTradeRow, ExperimentRow, StrategyApprovalRow):
            await session.execute(delete(table))


TRADE_NEXT = {"min_confidence": 50.0, "min_risk_reward": 1.5}
CHECKS = {"random_entries": {"p_value": 0.01, "draws": 200}, "grid_positive": 0.8,
          "max_drawdown_r": 6.0}


async def _record(symbol="RELIANCE", timeframe="1h", strategy="engine", passed=True,
                  p_value=0.01, age_days=0.0, parameters=TRADE_NEXT, reason="measured",
                  data_source="angelone", expectancy=0.3, checks=CHECKS, code=None,
                  cost_profile="india:stock", quality_passed=True, with_experiment=True):
    """A walk-forward on record, as `research/engine_evidence.py` writes one:
    the row the gate reads and the experiment it points at."""
    created = time.time() - age_days * 86_400
    async with session_scope() as session:
        experiment_id = None
        if with_experiment:
            experiment = await registry.save(session, registry.build(
                kind="engine_walk_forward", stage="walk_forward", strategy=strategy,
                symbol=symbol, timeframe=timeframe, venue=data_source,
                cost_profile=cost_profile, verdict="PASSED" if passed else "NO MEASURED EDGE",
                datasets=[], window={}, parameters={}, assumptions={}, metrics={},
                reasons=[reason], now=created))
            experiment_id = experiment["id"]
        session.add(BacktestRow(
            symbol=symbol, timeframe=timeframe, strategy="walk_forward:" + strategy,
            bars=3000, metrics={}, created_at=created,
            payload={"edge": {"passed": passed, "reason": reason, "p_value": p_value,
                              "trades": 80, "method": "walk_forward",
                              "expectancy": expectancy, "ci_low": expectancy - 0.2,
                              "ci_high": expectancy + 0.2},
                     "next_parameters": parameters or {},
                     "data_source": data_source, "quality_passed": quality_passed,
                     "experiment_id": experiment_id, "cost_profile": cost_profile,
                     "code": versions.current() if code is None else code,
                     "checks": checks}))


async def _approve(symbol="RELIANCE", timeframe="1h", version=None):
    async with session_scope() as session:
        await approvals.approve(
            session, strategy="engine", symbol=symbol, timeframe=timeframe,
            strategy_version=version or versions.strategy_version("engine"),
            experiment_id="exp-test", approved_by="the trader")


def _cleared(symbol="RELIANCE", timeframe="1h", **record):
    """Everything a live order needs: a passing test on record, enough paper
    trades that agree with it, and a person's approval."""
    run(_record(symbol=symbol, timeframe=timeframe, **record))
    run(_paper(25, +0.4, symbol=symbol, timeframe=timeframe))
    run(_approve(symbol, timeframe))


async def _paper(count, r_multiple, symbol="RELIANCE", timeframe="1h"):
    async with session_scope() as session:
        for _ in range(count):
            session.add(PaperTradeRow(
                symbol=symbol, timeframe=timeframe, side="LONG", status="closed",
                entry=100.0, stop_loss=95.0, realised_r=r_multiple))


async def _edge(symbol="RELIANCE", timeframe="1h"):
    async with session_scope() as session:
        return await latest_edge(session, symbol, timeframe)


@pytest.fixture
def evidence(monkeypatch):
    """A clean slate, with the broker path declared validated — the tests
    that are about that switch turn it back off."""
    monkeypatch.setattr(settings, "live_broker_validated", True)
    run(_wipe_evidence())
    yield
    run(_wipe_evidence())


def test_no_test_on_record_means_no_edge(evidence):
    edge = run(_edge())
    assert edge["passed"] is False
    assert "no walk-forward test" in edge["reason"]


def test_only_a_walk_forward_of_the_live_engine_counts(evidence):
    run(_record(strategy="breakout"))                  # a named strategy passed
    assert run(_edge())["passed"] is False
    run(_record(strategy="engine", passed=False, reason="No measured edge: …"))
    edge = run(_edge())
    assert edge["passed"] is False
    assert edge["reason"] == "No measured edge: …"
    _cleared(strategy="engine", parameters={"min_confidence": 60.0,
                                            "min_risk_reward": 2.0})
    edge = run(_edge())                                # the newest one decides
    assert edge["passed"] is True
    assert (edge["min_confidence"], edge["min_risk_reward"]) == (60.0, 2.0)
    assert run(_edge(timeframe="15m"))["passed"] is False, "another timeframe is another test"


def test_a_past_edge_with_nothing_working_now_is_not_enough(evidence):
    run(_record(parameters={}))
    edge = run(_edge())
    assert edge["passed"] is False
    assert "most recent stretch" in edge["reason"]


def test_evidence_goes_stale(evidence):
    run(_record(age_days=31))
    edge = run(_edge())
    assert edge["passed"] is False
    assert "more than 30 days old" in edge["reason"]


def test_a_pass_among_many_tests_has_to_be_stronger(evidence):
    _cleared(p_value=0.03)
    assert run(_edge())["passed"] is True, "alone, 0.03 clears 5%"
    for symbol in ("TCS", "INFY", "SBIN", "ITC"):
        run(_record(symbol=symbol, passed=False, p_value=0.6))
    edge = run(_edge())
    assert edge["passed"] is False
    assert "5 markets have been walk-forward tested" in edge["reason"]
    assert edge["tests_considered"] == 5

    run(_record(p_value=0.004))                        # 0.05 / 5 = 0.01
    assert run(_edge())["passed"] is True
    # Tests older than the window are not part of the family.
    run(_wipe_evidence())
    _cleared(p_value=0.03)
    for symbol in ("TCS", "INFY", "SBIN", "ITC"):
        run(_record(symbol=symbol, passed=False, p_value=0.6, age_days=40))
    assert run(_edge())["passed"] is True


def test_paper_trading_can_overrule_the_backtest(evidence):
    run(_record())
    run(_approve())
    run(_paper(12, -0.5))
    edge = run(_edge())
    assert edge["passed"] is False
    assert "Paper trading disagrees" in edge["reason"]
    assert edge["paper_trades"] == 12

    # Winning on paper, but nowhere near what the backtest promised.
    run(_wipe_evidence())
    run(_record(expectancy=0.6))
    run(_approve())
    run(_paper(25, +0.1))
    edge = run(_edge())
    assert edge["passed"] is False
    assert "Paper trading disagrees" in edge["reason"]
    assert edge["eligibility"]["state"] == "INELIGIBLE"


def test_a_backtest_alone_is_not_enough_it_has_to_be_paper_traded(evidence):
    """The history says yes. That used to be the whole of it."""
    run(_record())
    run(_approve())
    edge = run(_edge())
    assert edge["passed"] is False
    assert edge["eligibility"]["state"] == "RESEARCH APPROVED"
    assert "not been paper traded" in edge["reason"]

    run(_paper(5, +0.4))
    edge = run(_edge())
    assert edge["passed"] is False
    assert edge["eligibility"]["state"] == "PAPER VALIDATION"
    assert "5 of 20 paper trades" in edge["reason"]

    run(_paper(20, +0.4))
    assert run(_edge())["passed"] is True


def test_no_test_result_approves_itself(evidence):
    """Everything automatic has passed. It is eligible for review — which is
    not the same as approved, and nothing is sent until a person says so."""
    run(_record())
    run(_paper(25, +0.4))
    edge = run(_edge())
    assert edge["eligibility"]["state"] == "ELIGIBLE FOR HUMAN REVIEW"
    assert edge["passed"] is False and edge["approved"] is False
    assert "A person has to approve" in edge["reason"]

    run(_approve())
    edge = run(_edge())
    assert edge["passed"] is True and edge["approved"] is True
    assert edge["approval"]["approved_by"] == "the trader"


def test_an_approval_is_for_one_version_of_the_code_on_one_market(evidence):
    run(_record())
    run(_paper(25, +0.4))
    run(_approve(version="engine@000000000000"))        # given for code that has changed since
    assert run(_edge())["passed"] is False
    run(_approve(symbol="TCS"))
    assert run(_edge())["passed"] is False, "another market's approval is not this one's"
    run(_approve())
    assert run(_edge())["passed"] is True

    async def revoke_all():
        async with session_scope() as session:
            for row in await approvals.listing(session):
                await approvals.revoke(session, row["id"])
    run(revoke_all())
    assert run(_edge())["passed"] is False, "and it can be withdrawn"


def test_evidence_about_code_that_has_changed_does_not_count(evidence):
    """The engine was edited after the test. The test is about the old one."""
    stale = dict(versions.current(), engine="engine@aaaaaaaaaaaa")
    _cleared(code=stale)
    edge = run(_edge())
    assert edge["passed"] is False
    assert edge["eligibility"]["state"] == "INELIGIBLE"
    assert "has changed since this was tested" in edge["reason"]

    run(_wipe_evidence())
    _cleared(code=dict(versions.current(), costs="costs@bbbbbbbbbbbb"))
    assert "cost model has changed" in run(_edge())["reason"]


def test_a_result_for_another_venue_does_not_count(evidence):
    _cleared(cost_profile="test_perp")
    edge = run(_edge())
    assert edge["passed"] is False
    assert "different venue" in edge["reason"]


def test_a_test_that_did_not_beat_random_entries_does_not_count(evidence):
    _cleared(checks=dict(CHECKS, random_entries={"p_value": 0.4}))
    assert "No better than entries at random bars" in run(_edge())["reason"]
    run(_wipe_evidence())
    _cleared(checks=dict(CHECKS, random_entries=None))
    assert "not been compared with entries at random bars" in run(_edge())["reason"]
    run(_wipe_evidence())
    _cleared(checks=dict(CHECKS, grid_positive=0.2))
    assert "depends on one setting" in run(_edge())["reason"]
    run(_wipe_evidence())
    _cleared(checks=dict(CHECKS, max_drawdown_r=31.0))
    assert "deepest drawdown" in run(_edge())["reason"]


def test_a_test_from_before_experiments_were_recorded_does_not_count(evidence):
    _cleared(with_experiment=False)
    edge = run(_edge())
    assert edge["passed"] is False
    assert edge["eligibility"]["state"] == "INSUFFICIENT EVIDENCE"


def test_an_experiment_record_edited_afterwards_does_not_count(evidence):
    _cleared()

    async def tamper():
        async with session_scope() as session:
            row = (await session.execute(select(ExperimentRow))).scalars().first()
            row.record = dict(row.record, metrics={"expectancy_r": 9.9})
    run(tamper())
    edge = run(_edge())
    assert edge["passed"] is False
    assert "no longer matches its hash" in edge["reason"]


def test_running_the_same_test_until_it_passes_is_not_a_pass(evidence):
    for _ in range(6):
        run(_record(passed=False, p_value=0.6))
    _cleared()                                             # the seventh try
    edge = run(_edge())
    assert edge["passed"] is False
    assert "run 7 times" in edge["reason"]


def test_nothing_is_reviewable_until_the_broker_path_has_been_validated(
        evidence, monkeypatch):
    monkeypatch.setattr(settings, "live_broker_validated", False)
    _cleared()
    edge = run(_edge())
    assert edge["passed"] is False
    assert edge["eligibility"]["state"] == "PAPER VALIDATION"
    assert "LIVE_BROKER_VALIDATED" in edge["reason"]


def test_a_market_with_no_live_path_is_never_reviewable(evidence):
    _cleared(symbol="OTCUSD", data_source="elsewhere", cost_profile="generic")
    edge = run(_edge(symbol="OTCUSD"))
    assert edge["passed"] is False
    assert "Not tradable through an Indian broker" in edge["reason"]
    run(_wipe_evidence())
    _cleared(symbol="MCXCRUDE", cost_profile="india:commodity")
    assert "lots or units is unverified" in run(_edge(symbol="MCXCRUDE"))["reason"]


# ── the go-live gate ──────────────────────────────────────────

@pytest.fixture
def live(monkeypatch, evidence):
    from app.broker.angelone import angel_auth, angel_instruments
    from app.broker.trading_mode import PAPER, trading_mode
    from fake_angel import FakeAngel
    from test_execution import FAKE_MATCH, _fake_connect, _wipe_executions

    async def resolve(symbol):
        return FAKE_MATCH
    monkeypatch.setattr(angel_instruments, "resolve", resolve)
    monkeypatch.setattr(settings, "live_require_measured_edge", True)
    fake = FakeAngel().install(monkeypatch)
    run(_wipe_executions())
    _fake_connect()
    trading_mode.set_mode("LIVE", confirmed=True)
    trading_mode.deactivate_kill_switch()
    with TestClient(app) as client:
        yield client, fake
    trading_mode.set_mode(PAPER)
    angel_auth._session = None  # noqa: SLF001
    run(_wipe_executions())


def _execute(client, **kwargs):
    from test_execution import _seed_recommendation

    rec = run(_seed_recommendation(**kwargs))
    return client.post(f"/api/trades/{rec}/execute", json={"confirmed": True}).json()


def test_live_orders_are_refused_without_a_measured_edge(live):
    client, fake = live
    body = _execute(client)
    assert body["status"] == "REJECTED"
    assert "No measured edge" in body["error_message"]
    assert client.get("/api/backtest/edge?symbol=RELIANCE&timeframe=1h").json()[
        "passed"] is False

    run(_record(passed=False, reason="No measured edge: expectancy is −0.2R"))
    assert "expectancy is −0.2R" in _execute(client)["error_message"]
    assert fake.placed == []

    run(_record(passed=True))
    waiting = _execute(client)
    assert waiting["status"] == "REJECTED", "a passing backtest alone sends nothing"
    assert "not been paper traded" in waiting["error_message"]
    run(_paper(25, +0.4))
    assert "A person has to approve" in _execute(client)["error_message"]
    assert fake.placed == []

    run(_approve())
    assert _execute(client)["status"] == "OPEN"
    assert len(fake.placed) == 1


def test_a_measured_edge_covers_only_the_kind_of_trade_that_was_tested(live):
    """The walk-forward traded setups at 65%+ confidence and 2:1 or better.
    That says nothing about a 55% setup, or one that pays 1.5:1."""
    client, fake = live
    _cleared(parameters={"min_confidence": 65.0, "min_risk_reward": 2.0})

    weak = _execute(client, confidence=55.0)
    assert weak["status"] == "REJECTED"
    assert "65% or more" in weak["error_message"]

    thin = _execute(client, target=2530.0)              # 30 against 20: 1.5 to 1
    assert thin["status"] == "REJECTED"
    assert "reward:risk is 1.50" in thin["error_message"]
    assert fake.placed == []

    assert _execute(client, confidence=70.0, target=2540.0)["status"] == "OPEN"


def test_the_walk_forward_endpoint_records_what_the_gate_reads(live, monkeypatch):
    client, _ = live
    _fake_frame(monkeypatch, random_walk(n=3400, seed=21, vol=0.002))
    monkeypatch.setattr(replay, "signals", lambda *args, **kwargs: {})   # engine: all WAIT

    ran = client.post("/api/backtest/walk-forward", json={
        "symbol": "RELIANCE", "timeframe": "1h", "bars": 3000}).json()
    assert ran["strategy"] == "engine"
    assert ran["edge"]["passed"] is False
    edge = client.get("/api/backtest/edge?symbol=RELIANCE&timeframe=1h").json()
    assert edge["passed"] is False
    assert "nothing was measured" in edge["reason"]
    assert "nothing was measured" in _execute(client)["error_message"]
