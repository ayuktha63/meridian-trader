"""When an exchange is open, and what that makes of a bar's timestamp.

A market that trades around the clock has one rule: a bar opens on a
multiple of its length. An exchange with a session has three more, and a
history that ignores them is wrong in ways that do not show:

* **Bars are anchored to the open, not to the clock.** The NSE's hourly bars
  open at 09:15, 10:15 … 15:15 local time. On a UTC clock grid none of them
  is "on the hour", so a check written for crypto throws all of them away —
  and so it did, along with every daily bar, which the venue stamps at local
  midnight.
* **The last bar of the day is short.** 15:15 to 15:30 is a quarter of an
  hour. It is complete at the close, not sixty minutes after it opened.
* **Most of the calendar is closed.** Nights, weekends and holidays are not
  missing data. A morning with no bars on a day the exchange was open is.

A bar's time here is always the instant it opened, in UTC epoch seconds.
Daily and weekly bars are stamped at the session open of their local day —
not at local midnight, which for a zone east of Greenwich is the previous
day in UTC and would put a Monday's bar in last week and the first bar after
a date-cut on the wrong side of it.

Holidays are not listed here, on purpose: a hand-kept list is wrong the
first time the exchange changes one. `TradingCalendar` is *measured* — the
days on which a reference series actually traded — and a history is checked
against that.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional, Sequence

import numpy as np

DAY = 86_400
WEEK = 604_800


@dataclass(frozen=True)
class Session:
    name: str
    timezone: str
    #: Seconds east of UTC. Only zones without daylight saving belong here;
    #: one that has it needs a real time-zone database, not an offset.
    utc_offset: int
    #: Seconds after local midnight.
    open: int
    close: int

    @property
    def length(self) -> int:
        return self.close - self.open

    # ── days ──────────────────────────────────────────────────

    def local_day(self, times: Any) -> np.ndarray:
        """The local calendar day of each time, as days since the epoch."""
        shifted = np.asarray(times, dtype=float) + self.utc_offset
        return np.floor_divide(shifted, DAY).astype(np.int64)

    def day_open(self, days: Any) -> np.ndarray:
        """The instant the session opens on each local day."""
        return np.asarray(days, dtype=np.int64) * DAY - self.utc_offset + self.open

    @staticmethod
    def weekday(days: Any) -> np.ndarray:
        """Monday is 0. The epoch was a Thursday."""
        return (np.asarray(days, dtype=np.int64) + 3) % 7

    @staticmethod
    def iso(day: int) -> str:
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(days=int(day))).strftime(
            "%Y-%m-%d")

    # ── bars ──────────────────────────────────────────────────

    def bars_per_day(self, tf_seconds: int) -> int:
        """Bars in one full session — the short last one included."""
        return -(-self.length // tf_seconds) if tf_seconds < DAY else 1

    def bucket(self, times: Any, tf_seconds: int) -> np.ndarray:
        """The open time of the bar of this length that each time falls in."""
        times = np.asarray(times, dtype=float)
        days = self.local_day(times)
        if tf_seconds >= WEEK:
            return self.day_open(days - self.weekday(days)).astype(float)
        opens = self.day_open(days).astype(float)
        if tf_seconds >= DAY:
            return opens
        return opens + np.floor_divide(times - opens, tf_seconds) * tf_seconds

    def restamp(self, times: Any, tf_seconds: int) -> np.ndarray:
        """Daily and weekly bars, wherever in the day the venue stamped them,
        moved to the session open of their local day. Intraday bars are left
        alone: when one opened is a fact, not a convention."""
        times = np.asarray(times, dtype=float)
        return self.bucket(times, tf_seconds) if tf_seconds >= DAY else times

    def aligned(self, times: Any, tf_seconds: int) -> np.ndarray:
        """Whether each bar opens on this session's grid for its length."""
        times = np.asarray(times, dtype=float)
        return self.bucket(times, tf_seconds) == times

    def in_session(self, times: Any, tf_seconds: int) -> np.ndarray:
        """Whether each bar opens inside the regular session. A special
        sitting in the evening, or a pre-open auction, does not."""
        times = np.asarray(times, dtype=float)
        if tf_seconds >= DAY:
            return np.ones(len(times), dtype=bool)
        offset = times - self.day_open(self.local_day(times))
        return (offset >= 0) & (offset < self.length)

    def bar_end(self, times: Any, tf_seconds: int) -> np.ndarray:
        """When each bar is complete: its length after it opened, or the
        close of the session if that comes first."""
        times = np.asarray(times, dtype=float)
        days = self.local_day(times)
        if tf_seconds >= WEEK:
            return (self.day_open(days - self.weekday(days) + 4) + self.length).astype(float)
        closes = (self.day_open(days) + self.length).astype(float)
        if tf_seconds >= DAY:
            return closes
        return np.minimum(times + tf_seconds, closes)

    def describe(self) -> Dict[str, Any]:
        def clock(seconds: int) -> str:
            return "{:02d}:{:02d}".format(seconds // 3600, seconds % 3600 // 60)
        return {"name": self.name, "timezone": self.timezone,
                "utc_offset_seconds": self.utc_offset,
                "open": clock(self.open), "close": clock(self.close),
                "bar_time": "the instant the bar opened, UTC epoch seconds; daily and "
                            "weekly bars are stamped at the session open of their local day"}


#: NSE cash equities, regular session. India has no daylight saving.
NSE_EQUITY = Session("NSE_EQ", "Asia/Kolkata", 19_800, 9 * 3600 + 15 * 60,
                     15 * 3600 + 30 * 60)

SESSIONS: Dict[str, Session] = {NSE_EQUITY.name: NSE_EQUITY}


def by_name(name: Optional[str]) -> Optional[Session]:
    return SESSIONS.get(name) if name else None


@dataclass(frozen=True)
class TradingCalendar:
    """The days an exchange was open, as measured from a series that trades
    on every one of them — an index, or the agreement of many stocks."""
    days: frozenset
    first: int
    last: int
    source: str

    def within(self, first: int, last: int) -> np.ndarray:
        lo, hi = max(first, self.first), min(last, self.last)
        return np.array(sorted(d for d in self.days if lo <= d <= hi), dtype=np.int64)

    @property
    def fingerprint(self) -> str:
        """Names the calendar by the days in it: two that agree on every day
        are the same calendar, wherever each was measured from."""
        body = ",".join(str(day) for day in sorted(self.days))
        return hashlib.sha256("{}|{}|{}".format(self.first, self.last, body).encode()).hexdigest()[:16]

    def describe(self) -> Dict[str, Any]:
        return {"source": self.source, "days": len(self.days),
                "first": Session.iso(self.first), "last": Session.iso(self.last),
                "fingerprint": self.fingerprint}


def calendar_from_times(session: Session, times: Iterable[float], source: str) -> TradingCalendar:
    """The local days on which a reference series has a bar."""
    days = np.unique(session.local_day(np.fromiter(times, dtype=float)))
    if not len(days):
        raise ValueError("A calendar needs at least one traded day.")
    return TradingCalendar(frozenset(int(d) for d in days), int(days[0]), int(days[-1]), source)


def calendar_from_universe(session: Session, series: Sequence[Sequence[float]], source: str,
                           quorum: float = 0.5, min_series: int = 3) -> TradingCalendar:
    """The days on which at least `quorum` of the series that were listed at
    the time had a bar. A day one stock sat out is that stock's gap; a day
    most of them sat out was a holiday.

    Only the stretch where `min_series` or more were listed is covered — a
    calendar resting on one stock's word is not a calendar.
    """
    day_sets = [np.unique(session.local_day(np.asarray(t, dtype=float))) for t in series if len(t)]
    if len(day_sets) < min_series:
        raise ValueError("A universe calendar needs at least {} series.".format(min_series))
    lo = min(int(d[0]) for d in day_sets)
    hi = max(int(d[-1]) for d in day_sets)
    present = np.zeros(hi - lo + 1, dtype=np.int64)
    listed = np.zeros(hi - lo + 1, dtype=np.int64)
    for days in day_sets:
        present[days - lo] += 1
        listed[int(days[0]) - lo:int(days[-1]) - lo + 1] += 1
    covered = listed >= min_series
    if not covered.any():
        raise ValueError("No stretch has {} series listed at once.".format(min_series))
    open_days = covered & (present >= np.ceil(quorum * listed))
    index = np.flatnonzero(covered)
    return TradingCalendar(frozenset(int(i) + lo for i in np.flatnonzero(open_days)),
                           int(index[0]) + lo, int(index[-1]) + lo, source)
