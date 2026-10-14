"""Exchange hours: when each Indian segment is open, and the grid its bars
sit on. Backs `is_market_open`, the live candle store and the market clock."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from . import holidays
from ..schemas.market import MarketClock, MarketSession

log = logging.getLogger("meridian.sessions")
_warned_stale_calendar = False

#: Indian exchange hours in UTC (IST is UTC+5:30). Weekdays only.
#: CDS currency futures 09:00-17:00 IST, NSE equity/F&O 09:15-15:30 IST,
#: MCX 09:00-23:30 IST.
INDIA_HOURS = {
    "currency_fut": (3.5, 11.5),
    "index": (3.75, 10.0),
    "equity": (3.75, 10.0),
    "commodity": (3.5, 18.0),
}


def session_key(asset_class: str) -> str:
    """Instrument asset class -> the key its exchange hours are filed under."""
    return "equity" if asset_class == "stock" else asset_class


#: India has no daylight saving, so its clock is a fixed distance from UTC.
IST_OFFSET_SECONDS = 19_800
DAY_SECONDS, WEEK_SECONDS = 86_400, 604_800


@dataclass(frozen=True)
class BarGrid:
    """Where an exchange's bars open.

    A market that never shuts has one rule: a bar opens on a multiple of its
    length since the epoch. An exchange has a session, and its bars are
    counted from the session's open — the NSE's hourly bars open at 09:15,
    10:15 … local time, none of them "on the hour" in UTC — while its daily
    bars are stamped at local midnight, which for India is 18:30 UTC the day
    before. A live bar has to be opened where the venue's own history puts
    it, or the two are different bars with the same name.
    """
    utc_offset: int
    #: Seconds after local midnight.
    open: int
    close: int

    def local_midnight(self, ts: float) -> int:
        return ((int(ts) + self.utc_offset) // DAY_SECONDS) * DAY_SECONDS - self.utc_offset

    def in_session(self, ts: float) -> bool:
        """Whether the exchange's regular session is open at this time of
        day. Says nothing of weekends or holidays: a tick that shows no
        trading opens no bar whatever the clock says (`CandleSeries`)."""
        return self.open <= int(ts) - self.local_midnight(ts) < self.close

    def bucket(self, ts: float, tf_seconds: int) -> int:
        """The open time of the bar of this length that `ts` falls in."""
        midnight = self.local_midnight(ts)
        if tf_seconds >= WEEK_SECONDS:
            # Monday is 0; the epoch was a Thursday.
            weekday = ((midnight + self.utc_offset) // DAY_SECONDS + 3) % 7
            return midnight - weekday * DAY_SECONDS
        if tf_seconds >= DAY_SECONDS:
            return midnight
        start = midnight + self.open
        return start + ((int(ts) - start) // tf_seconds) * tf_seconds

    def bar_end(self, open_time: float, tf_seconds: int) -> int:
        """When the bar that opened at `open_time` is complete: its length
        after it opened, or the close of the session if that comes first —
        the NSE's last hourly bar is fifteen minutes long, and a daily bar
        is finished at 15:30, not at the next midnight."""
        midnight = self.local_midnight(open_time)
        if tf_seconds >= WEEK_SECONDS:
            return midnight + 4 * DAY_SECONDS + self.close      # Friday's close
        if tf_seconds >= DAY_SECONDS:
            return midnight + self.close
        return min(int(open_time) + tf_seconds, midnight + self.close)


def bar_grid(asset_class: str) -> Optional[BarGrid]:
    """The grid for an Indian exchange segment, or None for a market that is
    not one — which keeps the plain clock grid."""
    hours = INDIA_HOURS.get(session_key(asset_class))
    if hours is None:
        return None
    start, end = hours
    return BarGrid(IST_OFFSET_SECONDS, int(round(start * 3600)) + IST_OFFSET_SECONDS,
                   int(round(end * 3600)) + IST_OFFSET_SECONDS)


def minutes_to_close(ts: float, asset_class: str) -> Optional[float]:
    """Minutes until this segment's session ends, for an Indian exchange
    instrument while its market is open. None otherwise — a market that never
    closes, or one that is shut right now, has no "minutes to the close"."""
    key = session_key(asset_class)
    if key not in INDIA_HOURS or not is_market_open(ts, key):
        return None
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    clock = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    return (INDIA_HOURS[key][1] - clock) * 60.0


def is_market_open(ts: float, asset_class: str = "fx") -> bool:
    """Indian exchange-traded instruments follow their own weekday sessions,
    by the clock, and are shut on the exchange's own full-day holidays
    (`data/holidays.py`) — a hand-kept list, checked against the years it
    actually covers; see that module for why and what it cannot represent
    (MCX's morning-only closures). Anything that is not an exchange product
    (nothing the application lists; test fixtures only) is treated as a
    market shut from Friday 21:00 UTC to Sunday 21:00 UTC.
    """
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    weekday, hour = dt.weekday(), dt.hour  # Monday = 0

    if asset_class in INDIA_HOURS:
        if weekday >= 5:
            return False
        local_date = datetime.fromtimestamp(ts + IST_OFFSET_SECONDS, tz=timezone.utc).date()
        if not holidays.covers(local_date.year):
            global _warned_stale_calendar
            if not _warned_stale_calendar:
                _warned_stale_calendar = True
                log.warning(
                    "No holiday calendar loaded for %s — data/holidays.py only covers "
                    "%s. A live order can go out on a day the exchange is actually "
                    "shut until this is updated.", local_date.year, holidays.COVERS_YEARS)
        elif holidays.is_full_holiday(local_date, session_key(asset_class)):
            return False
        start, end = INDIA_HOURS[asset_class]
        clock = hour + dt.minute / 60.0
        return start <= clock < end

    if weekday == 5:
        return False
    if weekday == 6 and hour < 21:
        return False
    if weekday == 4 and hour >= 21:
        return False
    return True


#: The segments Angel One trades, as the clock names them.
SEGMENTS: Tuple[Tuple[str, str], ...] = (
    ("NSE", "equity"), ("CDS", "currency_fut"), ("MCX", "commodity"))

#: Minutes after the open and before the close in which an exchange's volume
#: is concentrated — the two ends of the day's U.
BUSY_MINUTES = 30

CLOCK_BASIS = ("Exchange hours by the clock, Monday to Friday. Holidays and special "
               "sessions are not known to it: on a holiday it says open, and nothing trades.")


def market_clock(ts: Optional[float] = None, asset_class: Optional[str] = None) -> MarketClock:
    """Which Indian exchange segments are open, by the clock.

    `asset_class` names the segment the caller cares about; the active
    session and the liquidity reading are then that segment's alone.
    Otherwise they are the NSE's, the segment almost everything here trades
    on. (This was a forex clock — Sydney, Tokyo, London, New York — and the
    analyst was being told which of those was open while it read an NSE
    stock.)
    """
    ts = ts if ts is not None else time.time()
    now = datetime.fromtimestamp(ts, tz=timezone.utc)
    local = datetime.fromtimestamp(ts + IST_OFFSET_SECONDS, tz=timezone.utc)

    sessions: List[MarketSession] = []
    for name, key in SEGMENTS:
        grid = bar_grid(key)
        if is_market_open(ts, key):
            left = int((grid.local_midnight(ts) + grid.close - ts) // 60)
            sessions.append(MarketSession(name=name, open=True, closes_in_minutes=left))
            continue
        # The next weekday open: today's if it is still ahead, else the next.
        midnight, opens = grid.local_midnight(ts), None
        for ahead in range(8):
            candidate = midnight + ahead * DAY_SECONDS + grid.open
            weekday = ((candidate + IST_OFFSET_SECONDS) // DAY_SECONDS + 3) % 7
            if candidate > ts and weekday < 5:
                opens = int((candidate - ts) // 60)
                break
        sessions.append(MarketSession(name=name, open=False, opens_in_minutes=opens))

    wanted = session_key(asset_class) if asset_class else "equity"
    by_key = {key: session for (_, key), session in zip(SEGMENTS, sessions)}
    focus = by_key.get(wanted)
    if focus is None or not focus.open:
        regime, active = "closed", "closed"
    else:
        grid = bar_grid(wanted)
        since_open = (ts - (grid.local_midnight(ts) + grid.open)) / 60.0
        busy = since_open < BUSY_MINUTES or (focus.closes_in_minutes or 0) < BUSY_MINUTES
        regime, active = ("high" if busy else "normal"), focus.name

    return MarketClock(
        utc_time=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        ist_time=local.strftime("%Y-%m-%d %H:%M:%S IST"),
        sessions=sessions,
        active_session=active,
        liquidity_regime=regime,
        basis=CLOCK_BASIS,
    )
