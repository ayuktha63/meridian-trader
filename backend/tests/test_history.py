"""History deep enough to test on. A walk-forward needs several thousand
bars; a source that hands back one page of a thousand can never conclude."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.data.feed import MarketFeed
from app.data.providers import angelone as angel_module
from app.data.providers import coindcx as coindcx_module
from app.data.providers.angelone import AngelOneMarketDataProvider
from app.data.providers.base import MarketDataProvider, resample_rows
from app.data.providers.coindcx import CoinDCXProvider
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
        store.series("DOTUSD", "1h").replace([])
        return feed, source
    yield build
    store.series("DOTUSD", "1h").replace([])


def test_a_chart_is_content_with_what_is_there_but_a_backtest_can_ask_for_more(feed_with):
    feed, source = feed_with(depth=10_000)
    run(feed.ensure_history("DOTUSD", "1h"))
    assert len(store.series("DOTUSD", "1h")) == 1500
    run(feed.ensure_history("DOTUSD", "1h"))
    assert source.requests == [1500], "a chart never re-fetches a hydrated series"

    run(feed.ensure_history("DOTUSD", "1h", 4000))
    assert len(store.series("DOTUSD", "1h")) == 4000
    run(feed.ensure_history("DOTUSD", "1h", 4000))
    run(feed.ensure_history("DOTUSD", "1h", 3000))
    assert source.requests == [1500, 4000], "and what is already deep enough is left alone"


def test_a_source_that_has_run_out_is_not_asked_again(feed_with):
    """Asked for 4000, the provider had 1000. Asking again on every
    backtest would hammer it for the same answer."""
    feed, source = feed_with(depth=1000)
    run(feed.ensure_history("DOTUSD", "1h"))
    for _ in range(3):
        run(feed.ensure_history("DOTUSD", "1h", 4000))
    assert len(store.series("DOTUSD", "1h")) == 1000
    assert source.requests == [1500], "the first answer already showed how deep it goes"


def test_a_deeper_request_never_shrinks_what_is_held(feed_with):
    feed, source = feed_with(depth=10_000)
    run(feed.ensure_history("DOTUSD", "1h", 3000))
    source.depth = 50                       # the source has a bad moment
    run(feed.ensure_history("DOTUSD", "1h", 5000))
    assert len(store.series("DOTUSD", "1h")) == 3000


def test_a_request_is_capped_at_what_the_store_can_hold(feed_with):
    feed, source = feed_with(depth=100_000)
    run(feed.ensure_history("DOTUSD", "1h", 50_000))
    assert source.requests == [6000]
    assert len(store.series("DOTUSD", "1h")) == 6000


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


# ── CoinDCX ───────────────────────────────────────────────────

class FakeCoinDCX:
    """The candles endpoint as probed live: at most 1000 a request, the
    latest ones unless *both* ends of a window are given."""

    def __init__(self, listed: int, step: int = HOUR) -> None:
        self.step_ms = step * 1000
        self.newest = 1_790_000_000_000 - 1_790_000_000_000 % self.step_ms
        self.oldest = self.newest - (listed - 1) * self.step_ms
        self.calls = []
        #: Bar times the venue simply does not have — an outage.
        self.missing = set()

    async def get(self, url, params=None):
        self.calls.append(dict(params))
        limit = params["limit"]
        assert limit <= 1000, "the real API answers 422"
        end = self.newest
        if "startTime" in params and "endTime" in params:
            end = params["endTime"] - params["endTime"] % self.step_ms
        times = [end - i * self.step_ms for i in range(limit)]
        times = [t for t in times if t >= max(self.oldest, params.get("startTime", 0))
                 and t not in self.missing]
        body = [{"time": t, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 3}
                for t in times]                     # newest first, like the API
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)


def _coindcx(monkeypatch, listed):
    monkeypatch.setattr(coindcx_module, "PAGE_PAUSE_SECONDS", 0.0)
    provider, api = CoinDCXProvider(), FakeCoinDCX(listed)
    provider._client.get = api.get  # noqa: SLF001
    return provider, api


def test_coindcx_history_walks_back_page_by_page(monkeypatch):
    provider, api = _coindcx(monkeypatch, listed=50_000)
    rows = run(provider.history("BTCUSD", "1h", 2500))
    times = [r[0] for r in rows]
    assert len(rows) == 2500
    assert times == sorted(set(times)), "oldest first, no duplicates"
    assert times[-1] * 1000 == api.newest
    assert times[-1] - times[0] == 2499 * HOUR, "and no gaps between pages"
    assert [c["limit"] for c in api.calls] == [1000, 1000, 500]
    assert "endTime" not in api.calls[0]
    assert all("startTime" in c and "endTime" in c for c in api.calls[1:]), \
        "endTime alone is ignored by the API and returns the latest page again"


def test_coindcx_history_stops_at_the_start_of_the_listing(monkeypatch):
    provider, api = _coindcx(monkeypatch, listed=1300)
    rows = run(provider.history("BTCUSD", "1h", 5000))
    assert len(rows) == 1300
    assert len(api.calls) == 4, "two pages of bars, then two empty windows"


def test_coindcx_history_walks_past_a_hole_in_the_venues_own_data(monkeypatch):
    """An outage leaves a window with fewer than a full page. That used to be
    read as the start of the listing, and a market with eight years of
    history came back with two."""
    provider, api = _coindcx(monkeypatch, listed=50_000)
    hole = [api.newest - i * api.step_ms for i in range(1500, 1530)]
    api.missing = set(hole)
    rows = run(provider.deep_history("BTCUSD", "1h", 4000))
    times = [r[0] * 1000 for r in rows]
    assert len(rows) == 4000
    assert times[0] < min(hole), "it kept going on the far side"
    assert not set(times) & api.missing


def test_coindcx_chart_history_is_capped_but_research_history_is_not(monkeypatch):
    provider, _ = _coindcx(monkeypatch, listed=50_000)
    assert len(run(provider.history("BTCUSD", "1h", 20_000))) == coindcx_module.MAX_HISTORY
    assert len(run(provider.deep_history("BTCUSD", "1h", 20_000))) == 20_000


def test_coindcx_derived_timeframes_get_the_depth_they_need(monkeypatch):
    provider, api = _coindcx(monkeypatch, listed=50_000)
    rows = run(provider.history("BTCUSD", "4h", 1200))     # needs 4800 hourly bars
    assert len(rows) == 1200
    assert all(b[0] - a[0] == 4 * HOUR for a, b in zip(rows, rows[1:]))


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
    assert all(row[0] % (4 * HOUR) == 0 for row in rows), "on 4-hour boundaries"
    assert all(b[0] - a[0] >= 4 * HOUR for a, b in zip(rows, rows[1:]))
    assert max(row[5] for row in rows) > 1000, "several hourly bars in one"


def test_angel_has_no_guess_for_an_interval_it_does_not_offer(angel):
    provider, api = angel()
    assert run(provider.history("RELIANCE", "2h", 100)) == []
    assert api.calls == []
