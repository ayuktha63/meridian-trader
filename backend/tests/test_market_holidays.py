"""The market-hours check refuses a live order on a full-day exchange
holiday, not just outside the clock's weekday/session window.

docs/RISK_CONTROL_AUDIT.md finding 3: "Market-hours has no holiday
calendar... On an NSE holiday the check passes." This is the test that
fails without data/holidays.py wired into sessions.is_market_open.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.data import sessions


def _ts(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


def test_nse_equity_is_closed_on_republic_day():
    # 26 Jan 2026 is a Monday; 10:00 IST = 04:30 UTC, well inside the session.
    assert not sessions.is_market_open(_ts(2026, 1, 26, 4, 30), "equity")


def test_nse_equity_is_open_on_an_ordinary_monday_at_the_same_time():
    assert sessions.is_market_open(_ts(2026, 1, 19, 4, 30), "equity")


def test_currency_futures_share_the_nse_holiday_calendar():
    # Same Republic Day, CDS currency-future session.
    assert not sessions.is_market_open(_ts(2026, 1, 26, 5, 0), "currency_fut")


def test_mcx_is_closed_on_its_own_full_holiday():
    # Independence Day: MCX is closed all day, not only the NSE segments.
    assert not sessions.is_market_open(_ts(2026, 8, 15, 10, 0), "commodity")


def test_mcx_stays_open_in_the_evening_on_a_morning_only_mcx_holiday():
    # Ram Navami: NSE is fully closed, but MCX only closes its morning
    # session and trades the evening as usual. This app models one
    # continuous commodity session and must not block it outright.
    assert sessions.is_market_open(_ts(2026, 3, 26, 14, 0), "commodity")


def test_an_uncovered_year_falls_back_to_the_clock_rather_than_crashing():
    # 2027 has no holiday list yet (data/holidays.py is honest about that,
    # via `covers()`) — the check must still answer, by the clock, not raise.
    assert sessions.is_market_open(_ts(2027, 1, 26, 4, 30), "equity")


def test_holidays_module_knows_what_it_covers():
    from app.data import holidays

    assert holidays.covers(2026)
    assert not holidays.covers(2027)
    assert not holidays.covers(1999)
