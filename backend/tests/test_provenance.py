"""Where a bar came from. Simulated bars are for looking at: they are never
passed off as an exchange's, never blended with real ones, and never count
as evidence about a real market."""
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
SYMBOL = "DOTUSD"


def run(coro):
    return asyncio.run(coro)


class Exchange(MarketDataProvider):
    """A live venue whose history endpoint can be switched off."""
    name, live = "coindcx", True

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


class Simulator(MarketDataProvider):
    name, live = "synthetic", False

    def supports(self, symbol: str) -> bool:
        return True

    async def history(self, symbol, timeframe, bars):
        return [[T0 + i * HOUR, 1.0, 1.1, 0.9, 1.0, 10.0] for i in range(bars)]

    async def stream(self, symbols, handler):
        return None


@pytest.fixture
def market(monkeypatch):
    """A feed whose exchange for DOTUSD can fail, with a simulator behind it."""
    feed, exchange = MarketFeed(), Exchange()
    feed.providers = {"synthetic": Simulator(), "coindcx": exchange}
    monkeypatch.setattr(feed_module, "resolve_provider", lambda symbol, providers: exchange)
    monkeypatch.setattr(feed_module, "REAL_SOURCE_RETRY_SECONDS", 0.0)
    store.series(SYMBOL, "1h").replace([])
    yield feed, exchange
    store.series(SYMBOL, "1h").replace([])


def test_bars_from_the_simulator_are_never_labelled_as_an_exchanges(market):
    feed, exchange = market
    exchange.up = False
    run(feed.ensure_history(SYMBOL, "1h"))
    assert len(store.series(SYMBOL, "1h")) > 0, "the chart still has something to draw"
    assert feed.provider_for(SYMBOL) == "coindcx", "the venue that should have answered"
    assert feed.source_of(SYMBOL, "1h") == "synthetic", "but that is not where these came from"


def test_real_ticks_are_not_folded_into_simulated_history(market):
    """The exchange's history failed but its quotes work. Folding an 83,000
    quote into bars simulated around 1.00 makes a chart of nothing."""
    feed, exchange = market
    exchange.up = False
    run(feed.ensure_history(SYMBOL, "1h"))
    before = store.series(SYMBOL, "1h").rows()
    tick = ProviderTick(symbol=SYMBOL, time=before[-1][0] + 2 * HOUR, price=83_000.0,
                        bid=82_999.0, ask=83_001.0)
    run(feed._on_tick(tick, "coindcx"))                    # noqa: SLF001
    assert store.series(SYMBOL, "1h").rows() == before
    assert feed.quote(SYMBOL).last == 83_000.0, "the quote itself is real, and shown"


def test_simulated_history_is_replaced_as_soon_as_the_exchange_answers(market):
    feed, exchange = market
    exchange.up = False
    run(feed.ensure_history(SYMBOL, "1h"))
    exchange.up = True
    run(feed.ensure_history(SYMBOL, "1h"))
    series = store.series(SYMBOL, "1h")
    assert feed.source_of(SYMBOL, "1h") == "coindcx"
    assert {row[4] for row in series.rows()} == {100.0}, "nothing simulated is left in it"


def test_a_failing_exchange_is_not_asked_again_on_every_request(market, monkeypatch):
    feed, exchange = market
    monkeypatch.setattr(feed_module, "REAL_SOURCE_RETRY_SECONDS", 60.0)
    exchange.up = False
    for _ in range(5):
        run(feed.ensure_history(SYMBOL, "1h"))
    assert exchange.requests == 1


def test_real_bars_are_labelled_with_their_venue(market):
    feed, _ = market
    run(feed.ensure_history(SYMBOL, "1h"))
    assert feed.source_of(SYMBOL, "1h") == "coindcx"
    assert feed.source_of(SYMBOL, "4h") is None, "a series nobody loaded has no source"


# ── simulated bars are not evidence ───────────────────────────

def test_a_backtest_on_simulated_bars_says_so_and_cannot_pass(monkeypatch):
    """The suite runs on the simulator. Whatever the numbers say there, the
    verdict is not about a market."""
    monkeypatch.setattr(bt, "edge_verdict", lambda trades, method, seed=7: EdgeVerdict(
        passed=True, reason="looks wonderful", trades=80, expectancy=0.9, p_value=0.0001,
        method=method))
    result = run(run_backtest(BacktestRequest(symbol="EURUSD", timeframe="1h", bars=400,
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
