"""Bars built from live quotes have to be the venue's bars.

Seen on the running application on 13 October 2026, with Angel One
connected: NIFTY's daily series held two bars for the same day (one from the
history, stamped at local midnight; one built from quotes, stamped at UTC
midnight and opened at whatever the price was when the history loaded), and
RELIANCE's hourly series a bar opening at 09:30 after the history's 09:15.
These tests hold the live store to the grid the history is stamped on.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.data.providers import angelone as provider_module
from app.data.providers.angelone import AngelOneMarketDataProvider
from app.data.providers.base import resample_rows
from app.data.sessions import bar_grid
from app.data.store import CandleSeries, bar_open_time

IST = timezone(timedelta(hours=5, minutes=30))
HOUR, DAY = 3600, 86_400
NSE = bar_grid("stock")


def ist(day: int, hour: int = 0, minute: int = 0, second: int = 0, month: int = 10) -> float:
    return datetime(2026, month, day, hour, minute, second, tzinfo=IST).timestamp()


# ── the grid itself ───────────────────────────────────────────

def test_an_exchange_bar_is_counted_from_the_session_open_not_from_the_clock():
    """10:40 on the NSE is in the hourly bar that opened at 10:15. On the
    UTC clock it is in one that opened at 10:30."""
    moment = ist(13, 10, 40)
    assert NSE.bucket(moment, HOUR) == ist(13, 10, 15)
    assert bar_open_time(moment, HOUR) == ist(13, 10, 30), "the clock grid, for contrast"
    assert NSE.bucket(moment, 900) == ist(13, 10, 30)
    assert NSE.bucket(moment, 1800) == ist(13, 10, 15)
    assert NSE.bucket(ist(13, 15, 29, 59), HOUR) == ist(13, 15, 15), "the short last bar"
    assert NSE.bucket(moment, 4 * HOUR) == ist(13, 9, 15)
    assert NSE.bucket(ist(13, 13, 15), 4 * HOUR) == ist(13, 13, 15)


def test_a_daily_bar_is_the_local_day_and_a_week_starts_on_local_monday():
    """Angel One stamps a daily bar at midnight in India — 18:30 UTC the day
    before. On the UTC grid Monday's bar belongs to the week before it."""
    assert NSE.bucket(ist(13, 9, 15), DAY) == ist(13)
    assert NSE.bucket(ist(13), DAY) == ist(13), "the venue's own stamp is on the grid"
    monday = ist(12)                                   # 12 October 2026 is a Monday
    assert datetime.fromtimestamp(monday, IST).weekday() == 0
    for day in range(12, 17):
        assert NSE.bucket(ist(day, 11, 0), 7 * DAY) == monday
    assert bar_open_time(monday, 7 * DAY) < monday - 6 * DAY + HOUR, \
        "the clock grid files Monday's bar under last week"


def test_each_segment_has_its_own_hours_and_a_market_off_any_exchange_has_no_grid():
    assert (NSE.open, NSE.close) == (9 * HOUR + 900, 15 * HOUR + 1800)
    assert bar_grid("index") == NSE
    assert bar_grid("commodity").open == 9 * HOUR and bar_grid("commodity").close == 23 * HOUR + 1800
    assert bar_grid("currency_fut").close == 17 * HOUR
    assert bar_grid("fx") is None and bar_grid("otc") is None
    assert NSE.in_session(ist(13, 9, 15)) and NSE.in_session(ist(13, 15, 29, 59))
    assert not NSE.in_session(ist(13, 9, 14, 59)) and not NSE.in_session(ist(13, 15, 30))


# ── what the store builds from quotes ─────────────────────────

def _loaded(timeframe: str, rows) -> CandleSeries:
    series = CandleSeries("RELIANCE", timeframe)
    series.replace(rows, source="angelone", grid=NSE)
    return series


def test_a_quote_joins_todays_daily_bar_instead_of_opening_a_second_one():
    series = _loaded("1d", [[ist(12), 1180.3, 1185.5, 1153.2, 1158.9, 11_338_332.0],
                            [ist(13), 1158.9, 1166.3, 1152.2, 1165.6, 2_060_339.0]])
    opened, bar = series.apply_price(ist(13, 9, 59), 1167.0, 500.0)
    assert opened is False and len(series) == 2
    assert bar == [ist(13), 1158.9, 1167.0, 1152.2, 1167.0, 2_060_839.0]


def test_a_quote_joins_the_hourly_bar_the_history_already_holds():
    series = _loaded("1h", [[ist(13, 9, 15), 1158.9, 1166.0, 1152.2, 1163.6, 1_881_455.0]])
    opened, _ = series.apply_price(ist(13, 9, 59, 50), 1165.6, 63_065.0)
    assert opened is False and len(series) == 1, "09:59 is still the 09:15 bar"
    opened, bar = series.apply_price(ist(13, 10, 15, 2), 1166.0, 100.0)
    assert opened is True and bar[0] == ist(13, 10, 15)


def test_nothing_is_built_outside_the_session():
    """The poll runs all night. Each quarter of an hour it used to open a
    flat bar at the last price."""
    series = _loaded("15m", [[ist(12, 15, 15), 1156.0, 1158.9, 1155.0, 1158.9, 900_000.0]])
    for moment in (ist(12, 15, 30, 3), ist(12, 20, 0), ist(13, 3, 0), ist(13, 9, 14, 58)):
        opened, bar = series.apply_price(moment, 1158.9 + 1.0, 10.0)
        assert opened is False and bar[0] == ist(12, 15, 15)
    assert len(series) == 1 and series.rows()[0][4] == 1158.9, "and the last bar is left alone"


def test_a_weekday_with_no_trading_builds_nothing_either():
    """A holiday is a weekday to the clock. What tells it from a trading day
    is that nothing trades: the price does not move and no volume is added."""
    series = _loaded("15m", [[ist(12, 15, 15), 1156.0, 1158.9, 1155.0, 1158.9, 900_000.0]])
    for minute in range(15, 60, 3):
        opened, _ = series.apply_price(ist(13, 9, minute), 1158.9, 0.0)
        assert opened is False
    assert len(series) == 1
    opened, bar = series.apply_price(ist(13, 10, 1), 1158.9, 25.0)      # a trade at that price
    assert opened is True and bar == [ist(13, 10, 0), 1158.9, 1158.9, 1158.9, 1158.9, 25.0]


def test_the_first_bar_of_a_session_opens_at_its_own_first_price():
    """Opened at yesterday's close, a gap of twenty rupees became the range
    of the first fifteen minutes."""
    series = _loaded("15m", [[ist(12, 15, 15), 1156.0, 1158.9, 1155.0, 1158.9, 900_000.0]])
    opened, bar = series.apply_price(ist(13, 9, 15, 2), 1180.0, 5_000.0)
    assert opened is True
    assert bar == [ist(13, 9, 15), 1180.0, 1180.0, 1180.0, 1180.0, 5_000.0]
    _, bar = series.apply_price(ist(13, 9, 20), 1178.5, 700.0)
    assert bar[1:5] == [1180.0, 1180.0, 1178.5, 1178.5]


def test_a_series_with_no_session_keeps_the_clock_grid():
    """A market that never shuts: as it was, on the clock, at any hour."""
    series = CandleSeries("EURUSD", "1h")
    start = float(int(ist(13, 3, 0)) // HOUR * HOUR)
    series.replace([[start, 1.10, 1.11, 1.09, 1.105, 0.0]], source="elsewhere")
    assert series.grid is None
    opened, bar = series.apply_price(start + HOUR + 5, 1.107, 0.0)
    assert opened is True and bar[:2] == [start + HOUR, 1.105], "opens at the previous close"


def test_reloading_with_nothing_clears_the_grid_with_the_source():
    series = _loaded("15m", [[ist(13, 9, 15), 1.0, 1.0, 1.0, 1.0, 0.0]])
    series.replace([])
    assert series.source is None and series.grid is None


# ── bars made from finer ones ─────────────────────────────────

def test_four_hour_and_weekly_bars_are_cut_on_the_session_too():
    hours = [[ist(13, 9, 15) + i * HOUR, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0]
             for i in range(7)]                                 # 09:15 … 15:15
    four = resample_rows(hours, 4 * HOUR, NSE)
    assert [bar[0] for bar in four] == [ist(13, 9, 15), ist(13, 13, 15)]
    assert [bar[5] for bar in four] == [40.0, 30.0]
    assert [bar[0] for bar in resample_rows(hours, 4 * HOUR)] != [bar[0] for bar in four]

    days = [[ist(day), 1.0, 2.0, 0.5, 1.5, 1.0] for day in (9, 12, 13, 14, 15, 16, 19)]
    weeks = resample_rows(days, 7 * DAY, NSE)
    assert [bar[0] for bar in weeks] == [ist(5), ist(12), ist(19)]
    assert [bar[5] for bar in weeks] == [1.0, 5.0, 1.0], "Monday to Friday is one week"


# ── the source says which grid its bars are on ────────────────

def test_angel_one_names_the_grid_for_each_market_it_serves():
    provider = AngelOneMarketDataProvider()
    assert provider.bar_grid("RELIANCE") == NSE
    assert provider.bar_grid("NIFTY") == NSE
    assert provider.bar_grid("MCXGOLD") == bar_grid("commodity")
    assert provider.bar_grid("EURUSD") is None, "not an exchange product"
    assert provider.bar_grid("NOPE") is None


def test_the_feed_loads_a_series_onto_its_sources_grid(monkeypatch):
    from app.data import feed as feed_module
    from app.data.store import store

    class Source:
        name, live = "angelone", True

        def supports(self, symbol):
            return True

        def bar_grid(self, symbol):
            return NSE

        async def history(self, symbol, timeframe, bars):
            return [[ist(12) + i * DAY, 1.0, 2.0, 0.5, 1.5, 1.0] for i in range(250)]

    market_feed = feed_module.MarketFeed()
    market_feed.providers = {"angelone": Source()}
    store.series("SBIN", "1d").replace([])
    asyncio.run(market_feed.ensure_history("SBIN", "1d"))
    series = store.series("SBIN", "1d")
    try:
        assert series.source == "angelone" and series.grid == NSE
    finally:
        series.replace([])


# ── volume in the unit the candles use ────────────────────────

def test_a_futures_quote_is_counted_in_contracts_like_its_candles(monkeypatch):
    """NIFTY27OCT26FUT on 13 October 2026: the history's bars held about
    5,000 and the bars built from quotes about 70,000. The quote's running
    total is in units; the candles are in lots of 65."""
    polls = [{"symbolToken": "53001", "ltp": 22_650.0, "tradeVolume": 1_000_000},
             {"symbolToken": "53001", "ltp": 22_651.0, "tradeVolume": 1_000_650},
             {"symbolToken": "53001", "ltp": 22_652.0, "tradeVolume": 1_013_650}]

    class Api:
        def getMarketData(self, mode, tokens):  # noqa: N802 - the SDK's name
            return {"status": True, "data": {"fetched": [polls.pop(0)]}}

    async def resolve(symbol):
        return SimpleNamespace(exchange="NFO", token="53001", lot_size=65)
    monkeypatch.setattr(provider_module.angel_instruments, "resolve", resolve)
    monkeypatch.setattr(provider_module.angel_auth, "ensure", lambda: Api())
    provider, ticks = AngelOneMarketDataProvider(), []

    async def handler(tick):
        ticks.append(tick)
    for _ in range(3):
        asyncio.run(provider._poll_once(["NIFTY"], handler))  # noqa: SLF001
    assert [tick.volume for tick in ticks] == [0.0, 10.0, 200.0]


def test_a_share_is_still_counted_in_shares(monkeypatch):
    polls = [{"symbolToken": "2885", "ltp": 1160.0, "tradeVolume": 500},
             {"symbolToken": "2885", "ltp": 1160.5, "tradeVolume": 900}]

    class Api:
        def getMarketData(self, mode, tokens):  # noqa: N802 - the SDK's name
            return {"status": True, "data": {"fetched": [polls.pop(0)]}}

    async def resolve(symbol):
        return SimpleNamespace(exchange="NSE", token="2885", lot_size=500)
    monkeypatch.setattr(provider_module.angel_instruments, "resolve", resolve)
    monkeypatch.setattr(provider_module.angel_auth, "ensure", lambda: Api())
    provider, ticks = AngelOneMarketDataProvider(), []

    async def handler(tick):
        ticks.append(tick)
    for _ in range(2):
        asyncio.run(provider._poll_once(["RELIANCE"], handler))  # noqa: SLF001
    assert [tick.volume for tick in ticks] == [0.0, 400.0]


@pytest.mark.parametrize("timeframe,seconds", [("15m", 900), ("1h", HOUR), ("1d", DAY)])
def test_a_whole_session_of_quotes_lands_on_the_bars_the_venue_would_send(timeframe, seconds):
    """Quotes every three seconds from before the open to after the close:
    every bar built opens where Angel One's own candle for it does."""
    series = _loaded(timeframe, [[NSE.bucket(ist(12, 15, 15), seconds), 1.0, 1.0, 1.0, 1.0, 1.0]])
    moment, price = ist(13, 9, 0), 1000.0
    while moment < ist(13, 16, 0):
        price += 0.05
        series.apply_price(moment, price, 3.0)
        moment += 3.0
    built = [row[0] for row in series.rows()[1:]]
    expected = {"15m": [ist(13, 9, 15) + i * 900 for i in range(25)],
                "1h": [ist(13, 9, 15) + i * HOUR for i in range(7)],
                "1d": [ist(13)]}[timeframe]
    assert built == expected


# ── one session, two places it is written down ────────────────

def test_the_live_grid_and_the_research_session_cannot_drift_apart():
    """Research (`research/exchange_calendar.py`) and the live store each
    hold the NSE's hours. They are the same hours, and cut the same bars; the
    one deliberate difference is the stamp on a daily bar — the live store
    keeps Angel One's (local midnight), research moves it to the open."""
    from app.research.exchange_calendar import NSE_EQUITY

    assert (NSE.utc_offset, NSE.open, NSE.close) == (
        NSE_EQUITY.utc_offset, NSE_EQUITY.open, NSE_EQUITY.close)
    times = [ist(13, 9, 15) + 37 * k for k in range(600)]          # across the session
    for seconds in (60, 300, 900, 1800, HOUR):
        live = [NSE.bucket(t, seconds) for t in times]
        assert live == [int(x) for x in NSE_EQUITY.bucket(times, seconds)]
        assert [NSE.bar_end(t, seconds) for t in live] == [
            int(x) for x in NSE_EQUITY.bar_end(live, seconds)]
    days = [ist(day, 11) for day in (12, 13, 14, 15, 16)]
    assert [NSE.bucket(t, DAY) + NSE.open for t in days] == [
        int(x) for x in NSE_EQUITY.bucket(days, DAY)]
    assert [NSE.bar_end(NSE.bucket(t, DAY), DAY) for t in days] == [
        int(x) for x in NSE_EQUITY.bar_end(days, DAY)]
    assert {NSE.bucket(t, 7 * DAY) + NSE.open for t in days} == {
        int(x) for x in NSE_EQUITY.bucket(days, 7 * DAY)}
