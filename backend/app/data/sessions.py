"""FX session clock — drives liquidity weighting, synthetic volume and the UI."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from ..schemas.market import MarketClock, MarketSession

# name -> (open_hour_utc, close_hour_utc, liquidity weight)
SESSIONS: Dict[str, Tuple[int, int, float]] = {
    "sydney": (21, 6, 0.55),
    "tokyo": (0, 9, 0.75),
    "london": (7, 16, 1.25),
    "newyork": (12, 21, 1.20),
}


def _in_window(hour: int, start: int, end: int) -> bool:
    return start <= hour < end if start < end else (hour >= start or hour < end)


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
    """Crypto never closes. FX/metals close Fri 21:00 UTC → Sun 21:00 UTC.

    Indian exchange-traded instruments follow their own weekday sessions —
    modelling them as 24/5 would put candles where no trading happened.
    """
    if asset_class == "crypto":
        return True

    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    weekday, hour = dt.weekday(), dt.hour  # Monday = 0

    if asset_class in INDIA_HOURS:
        if weekday >= 5:
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


def liquidity_factor(ts: float, asset_class: str = "fx") -> float:
    """Relative activity multiplier at `ts`; 1.0 is an average hour."""
    if asset_class == "crypto":
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        # Crypto is 24/7 but still peaks with US/EU hours.
        return 0.85 + 0.35 * (1.0 if 12 <= hour < 22 else 0.35)
    if not is_market_open(ts, asset_class):
        return 0.05

    if asset_class in INDIA_HOURS:
        # Indian volume clusters at the open and again into the close.
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        start, end = INDIA_HOURS[asset_class]
        clock = dt.hour + dt.minute / 60.0
        progress = (clock - start) / max(end - start, 1e-6)
        return 0.7 + 0.6 * (1.0 - abs(progress - 0.5) * 2) ** 0.5 + (
            0.4 if progress < 0.12 or progress > 0.88 else 0.0)

    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    factor = 0.35
    for _, (start, end, weight) in SESSIONS.items():
        if _in_window(hour, start, end):
            factor += weight * 0.5
    if 12 <= hour < 16:      # London / New York overlap
        factor *= 1.25
    return max(0.15, min(2.2, factor))


def active_sessions(ts: float) -> List[str]:
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    return [
        name for name, (start, end, _) in SESSIONS.items()
        if _in_window(hour, start, end)
    ]


def market_clock(ts: float = None) -> MarketClock:
    ts = ts if ts is not None else time.time()
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    hour = dt.hour
    open_now = is_market_open(ts, "fx")

    sessions: List[MarketSession] = []
    for name, (start, end, _) in SESSIONS.items():
        is_open = open_now and _in_window(hour, start, end)
        if is_open:
            closes = (end - hour) % 24
            sessions.append(MarketSession(
                name=name, open=True,
                closes_in_minutes=int(closes * 60 - dt.minute),
            ))
        else:
            opens = (start - hour) % 24
            sessions.append(MarketSession(
                name=name, open=False,
                opens_in_minutes=int(opens * 60 - dt.minute),
            ))

    live = [s.name for s in sessions if s.open]
    if not open_now:
        regime, active = "closed", "closed"
    elif 12 <= hour < 16:
        regime, active = "overlap", "london/newyork"
    elif len(live) >= 2:
        regime, active = "high", "/".join(live)
    elif live:
        regime, active = "normal", live[0]
    else:
        regime, active = "thin", "interbank"

    return MarketClock(
        utc_time=dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        sessions=sessions,
        active_session=active,
        liquidity_regime=regime,
    )
