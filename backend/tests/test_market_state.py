"""What the application says about the market's state has to be true of the
market it trades: an Indian exchange clock, and a quote that knows its age."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


from app.analysis import confidence
from app.data import feed as feed_module
from app.data.feed import STALE_QUOTE_SECONDS, MarketFeed
from app.data.sessions import market_clock
from app.schemas.market import QuoteSnapshot

IST = timezone(timedelta(hours=5, minutes=30))


def ist(day, hour=0, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=IST).timestamp()


# ── the clock ─────────────────────────────────────────────────

def _sessions(clock):
    return {session.name: session for session in clock.sessions}


def test_the_clock_is_the_indian_exchanges_and_names_no_forex_centre():
    clock = market_clock(ist(13, 10, 20))                      # a Tuesday, mid-morning
    assert clock.ist_time == "2026-10-13 10:20:00 IST"
    assert clock.utc_time == "2026-10-13 04:50:00 UTC"
    sessions = _sessions(clock)
    assert set(sessions) == {"NSE", "CDS", "MCX"}
    assert all(session.open for session in sessions.values())
    assert sessions["NSE"].closes_in_minutes == 5 * 60 + 10
    assert sessions["CDS"].closes_in_minutes == 6 * 60 + 40
    assert (clock.active_session, clock.liquidity_regime) == ("NSE", "normal")
    for word in ("london", "tokyo", "sydney", "newyork", "overlap", "interbank"):
        assert word not in clock.model_dump_json().lower()


def test_the_ends_of_the_session_are_the_busy_part():
    assert market_clock(ist(13, 9, 20)).liquidity_regime == "high"
    assert market_clock(ist(13, 12, 0)).liquidity_regime == "normal"
    assert market_clock(ist(13, 15, 10)).liquidity_regime == "high"


def test_after_the_close_only_the_commodity_exchange_is_open():
    clock = market_clock(ist(13, 18, 0))
    sessions = _sessions(clock)
    assert not sessions["NSE"].open and not sessions["CDS"].open and sessions["MCX"].open
    assert sessions["NSE"].opens_in_minutes == 15 * 60 + 15, "tomorrow at 09:15"
    assert (clock.active_session, clock.liquidity_regime) == ("closed", "closed")
    commodity = market_clock(ist(13, 18, 0), asset_class="commodity")
    assert (commodity.active_session, commodity.liquidity_regime) == ("MCX", "normal")


def test_a_weekend_points_at_monday_and_the_clock_admits_what_it_cannot_know():
    clock = market_clock(ist(17, 11, 0))                       # a Saturday
    sessions = _sessions(clock)
    assert not any(session.open for session in sessions.values())
    assert sessions["NSE"].opens_in_minutes == (24 + 22) * 60 + 15
    assert "Holidays" in clock.basis and "not known" in clock.basis


# ── a quote's age ─────────────────────────────────────────────

def _quote(symbol="RELIANCE", seconds_ago=0.0):
    return QuoteSnapshot(symbol=symbol, bid=1163.9, ask=1164.1, last=1164.0,
                         spread_pips=2.0, updated_at=time.time() - seconds_ago, live=True)


def test_a_quote_reports_its_age_and_goes_stale():
    market = MarketFeed()
    assert market.quote("RELIANCE") is None and market.fresh_quote("RELIANCE") is None

    market.quotes["RELIANCE"] = _quote(seconds_ago=2.0)
    fresh = market.quote("RELIANCE")
    assert fresh.stale is False and 1.5 < fresh.age_seconds < 4.0
    assert market.fresh_quote("RELIANCE") is not None

    market.quotes["RELIANCE"] = _quote(seconds_ago=STALE_QUOTE_SECONDS + 5)
    old = market.quote("RELIANCE")
    assert old.stale is True and old.age_seconds > STALE_QUOTE_SECONDS
    assert old.last == 1164.0, "the price is still reported — as what it is"
    assert market.fresh_quote("RELIANCE") is None
    assert market.status()["stale_quotes"] == ["RELIANCE"]
    assert market.quotes["RELIANCE"].stale is False, "the stored quote is not rewritten"


def test_a_quote_seeded_from_history_is_not_passed_off_as_one_just_received():
    market = MarketFeed()
    market._seed_quote("RELIANCE", 1158.9, seen_at=time.time() - 3600)  # noqa: SLF001
    quote = market.quote("RELIANCE")
    assert quote.last == 1158.9 and quote.stale is True
    assert market.fresh_quote("RELIANCE") is None


def test_the_auto_trader_opens_no_paper_trade_at_a_stale_price(monkeypatch):
    """The feed stops; the last quote is from before it did. The engine still
    says BUY from its completed bars — and nothing is filled, because there
    is no price to fill it at."""
    from app.routers import ws

    opened = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def get_account(session, currency=None):
        return SimpleNamespace()

    async def no_open_trade(session):
        return None

    async def open_trade(*args, **kwargs):
        opened.append(args)
        return SimpleNamespace(side="long", entry=1.0, stop_loss=0.9, take_profit=1.2,
                               model_dump=lambda mode=None: {})

    async def nothing(conn):
        return None

    monkeypatch.setattr(ws, "session_scope", lambda: Session())
    monkeypatch.setattr(ws.paper_repo, "get_account", get_account)
    monkeypatch.setattr(ws.paper_repo, "open_trade_for", no_open_trade)
    monkeypatch.setattr(ws.paper_repo, "open_trade", open_trade)
    monkeypatch.setattr(ws, "_emit_paper_state", nothing)
    monkeypatch.setattr(ws.autotrader, "evaluate_entry", lambda *a, **k: SimpleNamespace(
        action="enter", side="long", stop_loss=1150.0, take_profit=1190.0, reason="test"))

    sent = []

    async def send(message):
        sent.append(message)
    conn = SimpleNamespace(paper_trade=None, paper_status=None, send=send)
    result = SimpleNamespace(
        symbol="RELIANCE", timeframe="15m", price=1164.0, id="a1",
        confidence=SimpleNamespace(final_confidence=80.0),
        verdict=SimpleNamespace(source="rule_engine"))

    monkeypatch.setitem(feed_module.feed.quotes, "RELIANCE",
                        _quote(seconds_ago=STALE_QUOTE_SECONDS + 60))
    asyncio.run(ws._auto_trade_entry(conn, result))  # noqa: SLF001
    assert opened == [] and "no current quote" in conn.paper_status

    feed_module.feed.quotes.pop("RELIANCE")
    asyncio.run(ws._auto_trade_entry(conn, result))  # noqa: SLF001
    assert opened == [] and "none received" in conn.paper_status

    monkeypatch.setitem(feed_module.feed.quotes, "RELIANCE", _quote(seconds_ago=1.0))
    asyncio.run(ws._auto_trade_entry(conn, result))  # noqa: SLF001
    assert len(opened) == 1, "with a current quote the same signal is taken"


# ── what the score is called ──────────────────────────────────

def test_the_score_is_documented_as_a_score():
    doc = confidence.probability.__doc__
    assert "Not a probability" in doc and "Calibrated" not in doc


def test_the_api_no_longer_describes_itself_as_a_forex_tool():
    import app
    from app.main import app as api

    assert "forex" not in api.title.lower() and "forex" not in (app.__doc__ or "").lower()
    assert "Angel One" in api.title
