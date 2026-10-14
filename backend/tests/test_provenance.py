"""Where a bar came from. Every series says which source supplied it, a
venue that does not answer leaves the chart empty rather than drawn from
anywhere else, and generated bars never count as evidence about a market."""
from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import delete

from app.backtest import engine as bt
from app.backtest import run_backtest
from app.data import feed as feed_module
from app.data.feed import MarketFeed
from app.data.providers.base import MarketDataProvider, ProviderTick
from app.data.store import store
from app.db.models import BacktestRow
from app.db.session import init_db, session_scope
from app.schemas.journal import BacktestRequest, EdgeVerdict
from app.services.edge import latest_edge

HOUR = 3600
T0 = 1_700_000_000 // HOUR * HOUR
SYMBOL = "MCXSILVER"


def run(coro):
    return asyncio.run(coro)


class Exchange(MarketDataProvider):
    """A live venue whose history endpoint can be switched off."""
    name, live = "angelone", True

    def __init__(self) -> None:
        self.up, self.requests = True, 0

    def supports(self, symbol: str) -> bool:
        return True

    async def history(self, symbol, timeframe, bars):
        self.requests += 1
        if not self.up:
            return []
        return [[T0 + i * HOUR, 100.0, 101.0, 99.0, 100.0, 5.0] for i in range(min(bars, 3000))]

    async def stream(self, symbols, handler):
        return None


@pytest.fixture
def market(monkeypatch):
    """A feed whose one venue can stop answering."""
    feed, exchange = MarketFeed(), Exchange()
    feed.providers = {"angelone": exchange}
    monkeypatch.setattr(feed_module, "SOURCE_RETRY_SECONDS", 0.0)
    store.series(SYMBOL, "1h").replace([])
    yield feed, exchange
    store.series(SYMBOL, "1h").replace([])


def test_a_venue_that_does_not_answer_leaves_the_chart_empty(market):
    feed, exchange = market
    exchange.up = False
    run(feed.ensure_history(SYMBOL, "1h"))
    assert len(store.series(SYMBOL, "1h")) == 0, "nothing is drawn in its place"
    assert feed.source_of(SYMBOL, "1h") is None
    assert feed.provider_for(SYMBOL) == "angelone", "the venue that should have answered"
    assert feed.unsupplied() == [SYMBOL + ":1h"], "and it is on record as not having"


def test_a_quote_does_not_start_a_chart_the_venue_never_supplied(market):
    feed, exchange = market
    exchange.up = False
    run(feed.ensure_history(SYMBOL, "1h"))
    tick = ProviderTick(symbol=SYMBOL, time=T0 + 2 * HOUR, price=105.25,
                        bid=105.20, ask=105.30)
    run(feed._on_tick(tick, "angelone"))                   # noqa: SLF001
    assert len(store.series(SYMBOL, "1h")) == 0, "one price is not a history"
    assert feed.quote(SYMBOL).last == 105.25, "the quote itself is real, and shown"


def test_one_sources_quote_is_never_folded_into_anothers_bars(market):
    feed, _ = market
    store.series(SYMBOL, "1h").replace(
        [[T0 + i * HOUR, 1.0, 1.1, 0.9, 1.0, 10.0] for i in range(50)], source="elsewhere")
    before = store.series(SYMBOL, "1h").rows()
    tick = ProviderTick(symbol=SYMBOL, time=before[-1][0] + 2 * HOUR, price=105.25,
                        bid=105.20, ask=105.30)
    run(feed._on_tick(tick, "angelone"))                   # noqa: SLF001
    assert store.series(SYMBOL, "1h").rows() == before


def test_an_empty_chart_fills_as_soon_as_the_venue_answers(market):
    feed, exchange = market
    exchange.up = False
    run(feed.ensure_history(SYMBOL, "1h"))
    exchange.up = True
    run(feed.ensure_history(SYMBOL, "1h"))
    series = store.series(SYMBOL, "1h")
    assert feed.source_of(SYMBOL, "1h") == "angelone"
    assert {row[4] for row in series.rows()} == {100.0}
    assert feed.unsupplied() == []


def test_a_failing_venue_is_not_asked_again_on_every_request(market, monkeypatch):
    feed, exchange = market
    monkeypatch.setattr(feed_module, "SOURCE_RETRY_SECONDS", 60.0)
    exchange.up = False
    for _ in range(5):
        run(feed.ensure_history(SYMBOL, "1h"))
    assert exchange.requests == 1


def test_bars_are_labelled_with_the_venue_that_supplied_them(market):
    feed, _ = market
    run(feed.ensure_history(SYMBOL, "1h"))
    assert feed.source_of(SYMBOL, "1h") == "angelone"
    assert feed.source_of(SYMBOL, "4h") is None, "a series nobody loaded has no source"


def test_with_no_broker_session_there_is_no_source_and_no_bars(monkeypatch):
    """As the application runs: Angel One or nothing. Not connected, nothing
    serves any symbol, and the feed does not make something up."""
    from conftest import angel_only_providers

    monkeypatch.setattr(feed_module, "build_providers", angel_only_providers)
    assert angel_only_providers() == {}
    feed = MarketFeed()
    store.series(SYMBOL, "1h").replace([])
    run(feed.ensure_history(SYMBOL, "1h"))
    assert len(store.series(SYMBOL, "1h")) == 0
    assert feed.provider_for(SYMBOL) == "" and feed.is_live(SYMBOL) is False
    assert feed_module.provider_assignments([SYMBOL, "RELIANCE"], feed.providers) == {}


# ── simulated bars are not evidence ───────────────────────────

def test_a_backtest_on_simulated_bars_says_so_and_cannot_pass(monkeypatch):
    """The suite runs on generated bars. Whatever the numbers say there, the
    verdict is not about a market."""
    monkeypatch.setattr(bt, "edge_verdict", lambda trades, method, seed=7: EdgeVerdict(
        passed=True, reason="looks wonderful", trades=80, expectancy=0.9, p_value=0.0001,
        method=method))
    result = run(run_backtest(BacktestRequest(symbol="TATAMOTORS", timeframe="1h", bars=400,
                                              strategy="random")))
    assert result.data_source == "synthetic"
    assert result.edge.passed is False
    assert "simulated" in result.edge.reason
    assert any("simulated" in note.lower() for note in result.notes)


async def _evidence(data_source):
    await init_db()
    payload = {"edge": {"passed": True, "reason": "measured", "p_value": 0.001,
                        "trades": 80, "method": "walk_forward"},
               "next_parameters": {"min_confidence": 50.0, "min_risk_reward": 1.5}}
    if data_source is not None:
        payload["data_source"] = data_source
    async with session_scope() as session:
        await session.execute(delete(BacktestRow))
        session.add(BacktestRow(symbol="TCS", timeframe="1h", strategy="walk_forward:engine",
                                bars=3000, metrics={}, created_at=time.time(),
                                payload=payload))
    async with session_scope() as session:
        return await latest_edge(session, "TCS", "1h")


def test_the_live_gate_does_not_count_a_test_run_on_simulated_bars():
    edge = run(_evidence("synthetic"))
    assert edge["passed"] is False
    assert "simulated" in edge["reason"]


def test_the_live_gate_does_not_count_a_test_that_does_not_say_what_it_ran_on():
    edge = run(_evidence(None))
    assert edge["passed"] is False
    assert "does not record" in edge["reason"]
