"""The live engine decides on bars that have finished — the same bars the
backtest replays — and never on the one still forming.

As found, `engine.run` read the store as it stood: the chart's forming bar,
and every other timeframe's forming bar, fed by quotes up to the second. It
ran every 45 seconds, so most of its conclusions were about a bar no test
had ever seen.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.analysis import engine
from app.backtest.replay import FrameBuilder
from app.data.sessions import bar_grid
from app.data.store import COLUMNS, CandleSeries, bar_open_time, store
from app.instruments import MTF_LADDER, get_timeframe

IST = timezone(timedelta(hours=5, minutes=30))
NSE = bar_grid("stock")


def ist(day, hour=0, minute=0, second=0):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=IST).timestamp()


# ── which bar is still forming ────────────────────────────────

def test_a_bar_is_forming_until_its_length_has_passed_on_the_clock_grid():
    series = CandleSeries("EURUSD", "15m")
    series.replace([[900.0 * k, 1.0, 1.0, 1.0, 1.0, 0.0] for k in range(100, 105)])
    last = series.last_time
    assert series.forming(last + 1) and series.forming(last + 899)
    assert not series.forming(last + 900)
    assert len(series.completed_frame(last + 450)) == 4
    assert len(series.completed_frame(last + 900)) == 5
    assert list(series.completed_frame(last + 450, limit=2)["time"]) == [900.0 * 102, 900.0 * 103]
    assert len(series.frame()) == 5, "the store itself still holds the forming bar"


def test_on_an_exchange_a_bar_is_finished_at_the_close_if_that_comes_first():
    hourly = CandleSeries("RELIANCE", "1h")
    hourly.replace([[ist(13, 14, 15), 1.0, 1.0, 1.0, 1.0, 1.0],
                    [ist(13, 15, 15), 1.0, 1.0, 1.0, 1.0, 1.0]], source="angelone", grid=NSE)
    assert hourly.bar_end(ist(13, 15, 15)) == ist(13, 15, 30), "the short last bar"
    assert hourly.forming(ist(13, 15, 29)) and not hourly.forming(ist(13, 15, 30))

    daily = CandleSeries("RELIANCE", "1d")
    daily.replace([[ist(12), 1.0, 1.0, 1.0, 1.0, 1.0], [ist(13), 1.0, 1.0, 1.0, 1.0, 1.0]],
                  source="angelone", grid=NSE)
    assert daily.forming(ist(13, 11, 0)), "today's daily bar, mid-session"
    assert not daily.forming(ist(13, 15, 30)), "and done at the close, not at midnight"
    assert len(daily.completed_frame(ist(13, 11, 0))) == 1

    weekly = CandleSeries("RELIANCE", "1w")
    weekly.replace([[ist(12), 1.0, 1.0, 1.0, 1.0, 1.0]], source="angelone", grid=NSE)
    assert weekly.forming(ist(16, 15, 29)) and not weekly.forming(ist(16, 15, 30))


def test_an_empty_series_has_nothing_forming_and_nothing_completed():
    series = CandleSeries("EURUSD", "1h")
    assert not series.forming(time.time())
    assert len(series.completed_frame(time.time())) == 0


# ── the frames a decision is made on ──────────────────────────

def _bars(start: float, seconds: int, count: int, seed: int) -> list:
    rng = np.random.default_rng(seed)
    close = 1000.0 + np.cumsum(rng.normal(0, 1.0, count))
    return [[start + k * seconds, close[k] - 0.3, close[k] + 1.0, close[k] - 1.0, close[k],
             100.0 + k] for k in range(count)]


@pytest.fixture
def market():
    """A market held in the store as it would be mid-session: completed
    15-minute bars, then one that is forming and wildly different, with a
    1-minute and an hourly series that have both run on past the last close."""
    symbol = "SBIN"
    now = ist(13, 11, 7)                                  # seven minutes into the 11:00 bar
    last_closed = ist(13, 10, 45)
    quarter = _bars(last_closed - 299 * 900, 900, 300, seed=1)
    forming = [ist(13, 11, 0), 5000.0, 9000.0, 4000.0, 8000.0, 9e9]
    minute = _bars(ist(13, 11, 7) - 399 * 60, 60, 400, seed=2)
    hour_start = NSE.bucket(now, 3600)
    hourly = _bars(hour_start - 99 * 3600, 3600, 100, seed=3)
    hourly[-1] = [hour_start, 7000.0, 9000.0, 7000.0, 8000.0, 9e9]      # knows the forming bar
    loaded = {"15m": quarter + [forming], "1m": minute, "1h": hourly}
    for timeframe, rows in loaded.items():
        store.series(symbol, timeframe).replace(rows, source="angelone", grid=NSE)
    yield symbol, now, last_closed
    for timeframe in loaded:
        store.series(symbol, timeframe).replace([])


def test_no_frame_a_decision_reads_holds_anything_after_the_last_close(market):
    symbol, now, last_closed = market
    df = store.series(symbol, "15m").completed_frame(now, engine.ANALYSIS_BARS)
    assert df["time"].iloc[-1] == last_closed and df["high"].max() < 5000
    decided_at = last_closed + 900

    stored = {tf: store.frame(symbol, tf, 400) for tf in ("15m", "1m", "1h")}
    frames, daily = engine.frames_at_close(symbol, "15m", df, stored)

    assert frames["15m"]["time"].iloc[-1] == last_closed
    assert (frames["1m"]["time"] + 60 <= decided_at).all(), "finer bars: closed ones only"
    assert stored["1m"]["time"].iloc[-1] + 60 > decided_at, "though the store had later ones"
    hourly = frames["1h"]
    assert hourly["time"].iloc[-1] == NSE.bucket(last_closed, 3600)
    assert hourly["high"].max() < 5000, "the hour still forming is rebuilt from closed bars"
    assert hourly["close"].iloc[-1] == df["close"].iloc[-1]
    for frame in list(frames.values()) + ([daily] if daily is not None else []):
        assert frame["high"].max() < 5000 and frame["volume"].max() < 9e9


def test_live_frames_are_the_frames_the_backtest_builds_at_the_same_close(market):
    """One function for both: what the replay hands the engine at bar i is,
    frame for frame, what the live engine now hands it at that close."""
    symbol, now, _ = market
    df = store.series(symbol, "15m").completed_frame(now, engine.ANALYSIS_BARS)
    stored = {tf: store.frame(symbol, tf, 400) for tf in ("15m", "1h")}
    live, live_daily = engine.frames_at_close(symbol, "15m", df, stored)

    builder = FrameBuilder(df, "15m", {"1h": stored["1h"]}, NSE)
    replayed, replayed_daily = builder.frames(len(df) - 1), builder.daily(len(df) - 1)
    assert set(live) == set(replayed)
    for timeframe in replayed:
        pd.testing.assert_frame_equal(live[timeframe].reset_index(drop=True),
                                      replayed[timeframe].reset_index(drop=True))
    assert (live_daily is None) == (replayed_daily is None)
    if live_daily is not None:
        pd.testing.assert_frame_equal(live_daily, replayed_daily)


def test_higher_timeframes_from_another_source_are_rebuilt_not_borrowed(market):
    symbol, now, _ = market
    store.series(symbol, "1h").source = "elsewhere"
    df = store.series(symbol, "15m").completed_frame(now, engine.ANALYSIS_BARS)
    frames, _ = engine.frames_at_close(symbol, "15m", df, {"1h": store.frame(symbol, "1h", 400)})
    hourly = frames["1h"]
    assert hourly["time"].iloc[0] >= df["time"].iloc[0] - 3600, "built from the chart's own bars"


# ── the engine, end to end ────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def test_the_engines_conclusion_does_not_move_with_the_forming_bar(monkeypatch):
    """Analyse, let the forming bar run to three times the price on a wall of
    volume, analyse again: the same completed bars, the same conclusion."""
    seen = []
    real = engine.evaluate

    def spy(df, frames, instrument, timeframe, price, **kwargs):
        seen.append((df.copy(), {tf: f.copy() for tf, f in frames.items()}, price))
        return real(df, frames, instrument, timeframe, price, **kwargs)
    monkeypatch.setattr(engine, "evaluate", spy)

    symbol, timeframe = "EURUSD", "15m"
    first = _run(engine.run(symbol, timeframe, include_ai=False))
    assert first.bar_time, "the simulated market has history"
    series = store.series(symbol, timeframe)
    seconds = get_timeframe(timeframe).seconds
    now = time.time()
    if not series.forming(now):                      # make sure a bar is forming right now
        series.apply_price(now, series.last_close)
    spike = series.last_close * 3.0
    for tf in MTF_LADDER:
        if len(store.series(symbol, tf)):
            store.series(symbol, tf).apply_price(now, spike, 1e12)
    second = _run(engine.run(symbol, timeframe, include_ai=False))

    df, frames, price = seen[-1]
    assert df["time"].iloc[-1] < bar_open_time(now, seconds), "no forming bar in the window"
    assert df["high"].max() < spike and price == df["close"].iloc[-1]
    assert second.bar_time == int(df["time"].iloc[-1])
    for frame in frames.values():
        assert frame["high"].max() < spike, "nor in any other timeframe"
    if first.bar_time == second.bar_time:            # no bar closed between the two runs
        pd.testing.assert_frame_equal(seen[0][0], df)
        assert second.verdict.decision == first.verdict.decision
        assert second.confidence.final_confidence == first.confidence.final_confidence
        assert second.plan.entry == first.plan.entry


def test_the_price_shown_is_the_quote_and_the_price_decided_on_is_the_close(monkeypatch):
    from app.data.feed import feed
    from app.schemas.market import QuoteSnapshot

    decided = []
    real = engine.evaluate

    def spy(df, frames, instrument, timeframe, price, **kwargs):
        decided.append((price, float(df["close"].iloc[-1])))
        return real(df, frames, instrument, timeframe, price, **kwargs)
    monkeypatch.setattr(engine, "evaluate", spy)

    symbol = "EURUSD"
    _run(feed.ensure_history(symbol, "15m"))
    close = store.series(symbol, "15m").completed_frame(time.time())["close"].iloc[-1]
    monkeypatch.setitem(feed.quotes, symbol, QuoteSnapshot(
        symbol=symbol, bid=close * 1.0099, ask=close * 1.0101, last=close * 1.01,
        spread_pips=1.0, updated_at=time.time(), live=True))
    result = _run(engine.run(symbol, "15m", include_ai=False))
    price, last_close = decided[-1]
    assert price == last_close, "decided at the last completed close"
    assert result.price == pytest.approx(close * 1.01, rel=1e-4), "shown at the quote"


def test_columns_are_what_the_engine_expects():
    series = CandleSeries("EURUSD", "15m")
    series.replace([[900.0 * k, 1.0, 2.0, 0.5, 1.5, 3.0] for k in range(3)])
    assert list(series.completed_frame(900.0 * 2 + 1).columns) == list(COLUMNS)
