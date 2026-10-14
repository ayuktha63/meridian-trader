"""Full-day trading holidays for the Indian exchanges this app trades on.

`sessions.is_market_open` otherwise knows only the clock: Monday to Friday,
within the session hours. On a day the exchange is shut entirely, that check
passes anyway — a live order placed then would rest unfilled until the next
real session, which is exactly the "resting order, unknown gap" risk the
market-hours check exists to refuse. See docs/RISK_CONTROL_AUDIT.md, finding
3.

This is a hand-kept list, which is a defect in miniature by the same
reasoning `research/exchange_calendar.py` gives for why it refuses to keep
one: an exchange changes a date, most often for a festival pinned to the
lunar calendar, and a hand-kept list is wrong the moment that happens and
silently wrong until someone notices. Two things bound the damage:

* **It fails toward caution, not away from it.** A missing holiday here
  makes `is_market_open` wrong the same way it already was before this
  file existed — no worse. It never blocks a day that is genuinely open.
* **It is dated, and checked against its own range.** `holidays_loaded_for`
  tells a caller which years this file actually covers, so "the list is
  stale" can be answered instead of guessed at.

NSE equity, F&O and CDS currency futures share one holiday list. MCX trades
some of these days anyway, in an evening session this app does not model as
a separate session — see `MCX_FULL_HOLIDAYS`' own note.

Source: NSE/BSE's and MCX's published 2026 holiday calendars, read 14
October 2026 (https://www.nseindia.com, https://www.mcxindia.com, and
cross-checked against truedata.in/blog/trading-holidays-list-2026-nse-bse-and-mcx).
Re-verify every year — exchanges publish the next year's calendar in
December, and the lunar-calendar festivals move.
"""
from __future__ import annotations

from datetime import date
from typing import FrozenSet, Tuple

#: NSE/BSE equity, F&O and CDS — closed all day. 2026 calendar.
NSE_FULL_HOLIDAYS: FrozenSet[date] = frozenset({
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 3),    # Holi
    date(2026, 3, 26),   # Ram Navami
    date(2026, 3, 31),   # Mahavir Jayanti
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 5, 28),   # Bakri Id (Id-Ul-Zuha)
    date(2026, 6, 26),   # Muharram
    date(2026, 9, 14),   # Ganesh Chaturthi
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 9),   # Diwali Balipratipada
    date(2026, 11, 24),  # Guru Nanak Jayanti
    date(2026, 12, 25),  # Christmas
})

#: MCX — closed all day (both its morning and evening sessions). MCX treats
#: several of the NSE dates above as a *morning-only* closure, trading its
#: evening session as usual; this app models one continuous commodity
#: session and cannot represent a morning-only closure correctly, so those
#: partial-holiday dates are deliberately left OUT of this set rather than
#: wrongly blocking a session that is genuinely open.
MCX_FULL_HOLIDAYS: FrozenSet[date] = frozenset({
    date(2026, 1, 26),   # Republic Day
    date(2026, 2, 15),   # Mahashivratri
    date(2026, 3, 21),   # Id-Ul-Fitr
    date(2026, 4, 3),    # Good Friday
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 11, 8),   # Diwali Laxmi Pujan
    date(2026, 12, 25),  # Christmas
})

#: The years this file actually covers. `loaded_for` below is the honest
#: answer to "is this still current" — nothing silently assumes a year it
#: was never given.
COVERS_YEARS: Tuple[int, ...] = (2026,)


def holidays_for(segment: str) -> FrozenSet[date]:
    """The full-day holiday set for a segment key, as `sessions.py` names
    them: "commodity" gets MCX's own list; everything else — "equity",
    "currency_fut", "index" — shares NSE's."""
    return MCX_FULL_HOLIDAYS if segment == "commodity" else NSE_FULL_HOLIDAYS


def is_full_holiday(day: date, segment: str) -> bool:
    return day in holidays_for(segment)


def covers(year: int) -> bool:
    """Whether this file actually has a holiday list for `year` — not
    whether a given day in it happens to be a holiday."""
    return year in COVERS_YEARS
