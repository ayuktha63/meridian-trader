"""History deep enough to test on. A walk-forward needs several thousand
bars; a source that hands back one page of a thousand can never conclude."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.data.feed import MarketFeed
from app.data.providers import angelone as angel_module
from app.data.providers.angelone import AngelOneMarketDataProvider
from app.data.providers.base import MarketDataProvider, resample_rows
from app.data.store import store

HOUR = 3600


def run(coro):
    return asyncio.run(coro)


# ── the feed ──────────────────────────────────────────────────

class DeepSource(MarketDataProvider):
    """Serves up to `depth` hourly bars and counts how often it is asked."""
    name, live = "synthetic", False

    def __init__(self, depth: int) -> None:
        self.depth, self.requests = depth, []

    def supports(self, symbol: str) -> bool:
        return True

    async def history(self, symbol, timeframe, bars):
        self.requests.append(bars)
        count = min(bars, self.depth)
        return [[1_700_000_000.0 + i * HOUR, 1.0, 1.1, 0.9, 1.0, 10.0] for i in range(count)]

    async def stream(self, symbols, handler):
        return None


@pytest.fixture
def feed_with(monkeypatch):
    def build(depth):
        feed, source = MarketFeed(), DeepSource(depth)
        feed.providers = {"synthetic": source}
        store.series("JPYINR", "1h").replace([])
        return feed, source
    yield build
    store.series("JPYINR", "1h").replace([])


def test_a_chart_is_content_with_what_is_there_but_a_backtest_can_ask_for_more(feed_with):
    feed, source = feed_with(depth=10_000)
    run(feed.ensure_history("JPYINR", "1h"))
    assert len(store.series("JPYINR", "1h")) == 1500
    run(feed.ensure_history("JPYINR", "1h"))
    assert source.requests == [1500], "a chart never re-fetches a hydrated series"

    run(feed.ensure_history("JPYINR", "1h", 4000))
    assert len(store.series("JPYINR", "1h")) == 4000
    run(feed.ensure_history("JPYINR", "1h", 4000))
    run(feed.ensure_history("JPYINR", "1h", 3000))
    assert source.requests == [1500, 4000], "and what is already deep enough is left alone"


def test_a_source_that_has_run_out_is_not_asked_again(feed_with):
    """Asked for 4000, the provider had 1000. Asking again on every
    backtest would hammer it for the same answer."""
    feed, source = feed_with(depth=1000)
    run(feed.ensure_history("JPYINR", "1h"))
    for _ in range(3):
        run(feed.ensure_history("JPYINR", "1h", 4000))
    assert len(store.series("JPYINR", "1h")) == 1000
    assert source.requests == [1500], "the first answer already showed how deep it goes"


def test_a_deeper_request_never_shrinks_what_is_held(feed_with):
    feed, source = feed_with(depth=10_000)
    run(feed.ensure_history("JPYINR", "1h", 3000))
    source.depth = 50                       # the source has a bad moment
    run(feed.ensure_history("JPYINR", "1h", 5000))
    assert len(store.series("JPYINR", "1h")) == 3000


def test_a_source_is_prepared_before_the_feed_decides_what_to_stream(monkeypatch):
    """Angel One's scrip master is what says which stocks exist. It has to be
    read before the default symbols are, or the stocks are never streamed."""
    from app.data import feed as feed_module

    order = []

    class Source(DeepSource):
        async def prepare(self):
            order.append("prepare")
            raise RuntimeError("the master could not be fetched")   # …and that is survivable

        def supports(self, symbol):
            order.append("supports")
            return True

        async def stream(self, symbols, handler):
            order.append("stream " + ",".join(symbols))
            await asyncio.sleep(3600)

    async def go():
        feed = MarketFeed()
        monkeypatch.setattr(feed_module, "build_providers", lambda: {"synthetic": Source(300)})
        await feed.start(["JPYINR"])
        await asyncio.sleep(0)
        await feed.stop()
    try:
        run(go())
    finally:
        for timeframe in ("1m", "5m", "15m", "1h", "4h", "1d"):
            store.series("JPYINR", timeframe).replace([])
    assert order[0] == "prepare"
    assert "stream JPYINR" in order, "a source that could not prepare still serves what it can"


def test_a_request_is_capped_at_what_the_store_can_hold(feed_with):
    feed, source = feed_with(depth=100_000)
    run(feed.ensure_history("JPYINR", "1h", 50_000))
    assert source.requests == [6000]
    assert len(store.series("JPYINR", "1h")) == 6000


# ── resampling ────────────────────────────────────────────────

def test_resampled_bars_line_up_with_bars_built_from_ticks():
    from app.data.store import bar_open_time

    rows = [[1_700_000_000.0 + i * HOUR, 10.0 + i, 11.0 + i, 9.0 + i, 10.5 + i, 5.0]
            for i in range(24 * 21)]
    four_hour = resample_rows(rows, 4 * HOUR)
    assert all(bar[0] == bar_open_time(bar[0], 4 * HOUR) for bar in four_hour)
    first = [r for r in rows if bar_open_time(r[0], 4 * HOUR) == four_hour[1][0]]
    assert four_hour[1][1:] == [first[0][1], max(r[2] for r in first),
                                min(r[3] for r in first), first[-1][4], 20.0]

    weekly = resample_rows(rows, 7 * 86_400)
    for bar in weekly:
        assert datetime.utcfromtimestamp(bar[0]).weekday() == 0, "weeks start on Monday"
        assert bar[0] == bar_open_time(bar[0], 7 * 86_400)
    assert resample_rows([], HOUR) == []


# ── Angel One ─────────────────────────────────────────────────

class FakeSmartApi:
    """getCandleData for an exchange open 09:15-15:30 IST on weekdays."""

    def __init__(self, listed_days: int = 10_000, fail_after: int = 10_000) -> None:
        self.calls, self.listed_days, self.fail_after = [], listed_days, fail_after

    def getCandleData(self, params):  # noqa: N802 - the SDK's name
        self.calls.append(params)
        if len(self.calls) > self.fail_after:
            return {"status": False, "message": "Access rate exceeded"}
        start = datetime.strptime(params["fromdate"], "%Y-%m-%d %H:%M")
        end = datetime.strptime(params["todate"], "%Y-%m-%d %H:%M")
        first_listed = datetime(2026, 10, 9) - timedelta(days=self.listed_days)
        data, day = [], start.replace(hour=0, minute=0)
        while day <= end:
            if day.weekday() < 5 and day >= first_listed:
                for k in range(7):                       # 09:15 … 15:15
                    stamp = day.replace(hour=9, minute=15) + timedelta(hours=k)
                    if start <= stamp <= end:
                        data.append([stamp.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
                                     100.0, 101.0, 99.0, 100.5, 1000])
            day += timedelta(days=1)
        return {"status": True, "data": data}


@pytest.fixture
def angel(monkeypatch):
    def build(**kwargs):
        api = FakeSmartApi(**kwargs)

        async def resolve(symbol):
            return SimpleNamespace(exchange="NSE", token="2885")
        monkeypatch.setattr(angel_module.angel_instruments, "resolve", resolve)
        monkeypatch.setattr(angel_module.angel_auth, "ensure", lambda: api)
        monkeypatch.setattr(angel_module, "WINDOW_PAUSE_SECONDS", 0.0)
        return AngelOneMarketDataProvider(), api
    return build


def test_angel_history_walks_back_more_than_one_window(angel):
    provider, api = angel()
    rows = run(provider.history("RELIANCE", "1h", 3000))
    times = [r[0] for r in rows]
    assert len(rows) == 3000
    assert times == sorted(set(times))
    assert len(api.calls) >= 3, "one 180-day window holds about 900 hourly bars"
    assert all(c["interval"] == "ONE_HOUR" for c in api.calls)
    # Windows are contiguous: each ends where the one after it starts.
    for newer, older in zip(api.calls, api.calls[1:]):
        assert older["todate"] == newer["fromdate"]


def test_angel_history_asks_once_when_one_window_is_enough(angel):
    provider, api = angel()
    assert len(run(provider.history("RELIANCE", "1h", 500))) == 500
    assert len(api.calls) == 1


def test_angel_history_keeps_what_it_has_when_a_request_fails(angel):
    provider, api = angel(fail_after=2)
    rows = run(provider.history("RELIANCE", "1h", 5000))
    assert len(api.calls) == 3
    assert 1500 < len(rows) < 2100, "two windows' worth, not nothing"


def test_angel_history_stops_before_the_listing(angel):
    provider, api = angel(listed_days=200)
    rows = run(provider.history("RELIANCE", "1h", 5000))
    assert 0 < len(rows) < 1100
    assert len(api.calls) <= 4, "two empty windows end the search"


def test_angel_four_hour_bars_are_built_not_relabelled(angel):
    """A "4h" request used to return hourly candles under another name."""
    provider, api = angel()
    rows = run(provider.history("RELIANCE", "4h", 300))
    assert len(rows) == 300
    assert all(c["interval"] == "ONE_HOUR" for c in api.calls)
    # Counted from the session open — 09:15 and 13:15 in India — as the hourly
    # bars they are made of are. They used to be cut on UTC's four-hour
    # boundaries, which split the session 1 + 4 + 2 and matched nothing.
    opens = {(int(row[0]) + 19_800) % 86_400 for row in rows}
    assert opens == {9 * HOUR + 900, 13 * HOUR + 900}
    assert all(b[0] - a[0] >= 4 * HOUR for a, b in zip(rows, rows[1:]))
    assert max(row[5] for row in rows) > 1000, "several hourly bars in one"


def test_angel_has_no_guess_for_an_interval_it_does_not_offer(angel):
    provider, api = angel()
    assert run(provider.history("RELIANCE", "2h", 100)) == []
    assert api.calls == []



# ── Angel One quotes ──────────────────────────────────────────

class FakeQuotes:
    """getMarketData in FULL mode, one prepared answer per poll."""

    def __init__(self, *polls) -> None:
        self.polls, self.calls = list(polls), []

    def getMarketData(self, mode, tokens):  # noqa: N802 - the SDK's name
        self.calls.append((mode, tokens))
        return {"status": True, "data": {"fetched": [self.polls.pop(0)]}}


def _quote(ltp, day_volume, bid=None, ask=None):
    row = {"symbolToken": "2885", "ltp": ltp, "tradeVolume": day_volume}
    if bid is not None:
        row["depth"] = {"buy": [{"price": bid, "quantity": 40}, {"price": bid - 0.1, "quantity": 9}],
                        "sell": [{"price": ask, "quantity": 25}, {"price": ask + 0.1, "quantity": 7}]}
    return row


def _polled(monkeypatch, *polls):
    api = FakeQuotes(*polls)

    async def resolve(symbol):
        return SimpleNamespace(exchange="NSE", token="2885")
    monkeypatch.setattr(angel_module.angel_instruments, "resolve", resolve)
    monkeypatch.setattr(angel_module.angel_auth, "ensure", lambda: api)
    provider, ticks = AngelOneMarketDataProvider(), []

    async def handler(tick):
        ticks.append(tick)
    for _ in polls:
        run(provider._poll_once(["RELIANCE"], handler))  # noqa: SLF001
    assert all(call == ("FULL", {"NSE": ["2885"]}) for call in api.calls)
    return ticks


def test_an_angel_quote_carries_the_books_own_bid_and_ask(monkeypatch):
    tick, = _polled(monkeypatch, _quote(1412.5, 900_000, bid=1412.4, ask=1412.6))
    assert (tick.price, tick.bid, tick.ask) == (1412.5, 1412.4, 1412.6)


def test_a_quote_with_no_book_is_given_a_spread_and_never_none(monkeypatch):
    """An index has no order book, and a shut market has an empty one. A
    spread of zero would make every trade look free to the cost model."""
    no_book, empty_book, crossed = _polled(
        monkeypatch, _quote(24_500.0, 0), _quote(24_500.0, 0, bid=0.0, ask=0.0),
        _quote(24_500.0, 0, bid=24_501.0, ask=24_499.0))
    for tick in (no_book, empty_book, crossed):
        assert tick.bid < tick.price < tick.ask
        assert tick.ask - tick.bid == pytest.approx(24_500.0 * 0.0005)


def test_angel_never_reports_the_days_volume_as_a_ticks(monkeypatch):
    """`tradeVolume` is the running total for the day, and the store adds a
    tick's volume to the forming bar. Passed through, every three-second poll
    added the whole day again and buried the histogram behind one bar."""
    from app.data.store import CandleSeries

    ticks = _polled(monkeypatch, _quote(100.0, 1_000_000), _quote(100.5, 1_000_400),
                    _quote(100.5, 1_000_400), _quote(101.0, 1_002_000),
                    _quote(99.0, 150))            # a new session: the count starts again
    assert [tick.volume for tick in ticks] == [0.0, 400.0, 0.0, 1600.0, 150.0]

    series = CandleSeries("RELIANCE", "1h")
    series.replace([[1_700_000_000.0 // HOUR * HOUR, 100.0, 100.0, 100.0, 100.0, 0.0]])
    for offset, tick in enumerate(ticks[:4]):
        series.apply_price(series.last_time + 10 + offset, tick.price, tick.volume)
    assert series.rows()[-1][5] == 2000.0, "what traded while it formed, and no more"
