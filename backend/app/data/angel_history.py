"""Angel One's historical candles for research: asked for window by window,
and an honest account of what came back.

The chart's history call (`providers/angelone.py`) keeps whatever it has
when a request fails, because half a chart is better than none. A backtest
is the opposite case: a history with a hole nobody recorded produces a
result that looks like any other. So this fetch never returns bars without
a report beside them — every window asked for, how many tries it took, what
it returned — and the report says `complete` or says why not. Nothing here
fills a gap, and nothing here can place an order: it reads candles.

What is known about the API, and from where:

* Rate limit for `getCandleData`: 3 a second, 180 a minute, 5,000 an hour
  (SmartAPI forum, "Changes in API Rate Limit"). The pause below keeps to
  2.5 a second.
* Days per request: the windows in `providers/angelone.py`, which sit well
  inside the caps SmartAPI publishes per interval.
* Timestamps arrive as ISO-8601 with a +05:30 offset and are kept as UTC
  epoch seconds. A stamp with no offset is taken to be exchange time (IST),
  never the time zone of the machine that happens to be running this.

Not verified against a live failure, and said here rather than found out
later: the error codes that mean a session has expired. They are SmartAPI's
published ones; a code it does not recognise is treated as an ordinary
failure and retried a bounded number of times, which is the safe reading.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("meridian.angelone.history")

SOURCE_VERSION = "angel-history-1"

IST = timezone(timedelta(hours=5, minutes=30))

#: Meridian timeframe -> SmartAPI interval. Only what the API serves natively:
#: a coarser bar is built from these, on the session's grid, not asked for.
INTERVALS: Dict[str, str] = {
    "1m": "ONE_MINUTE", "5m": "FIVE_MINUTE", "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE", "1h": "ONE_HOUR", "1d": "ONE_DAY",
}

#: Days one request asks for, per interval — the same windows the chart uses.
WINDOW_DAYS: Dict[str, int] = {
    "ONE_MINUTE": 5, "FIVE_MINUTE": 30, "FIFTEEN_MINUTE": 60,
    "THIRTY_MINUTE": 90, "ONE_HOUR": 180, "ONE_DAY": 720,
}

PAUSE_SECONDS = 0.4
#: Tries per window, and the wait before the second (it doubles after that:
#: 3, 6, 12 and 24 seconds — a burst of refusals while the application loads
#: its charts lasts longer than a few seconds, so four quick tries did not
#: outlast it). The limit is counted per minute as well as per
#: second and is shared with everything else on the same API key, so a
#: request refused for rate has to be given time, not asked again at once.
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = 3.0
#: After this many windows in a row fail outright, stop asking: the API is
#: down or the session is gone, and hammering it helps neither.
MAX_CONSECUTIVE_FAILURES = 3
#: Empty windows in a row, older than data already received, that are taken
#: to mean "before the listing".
LISTING_EMPTY_WINDOWS = 2

#: SmartAPI's published codes for a token that is invalid, expired or
#: missing, and for a session that has ended.
AUTH_ERROR_CODES = frozenset({"AG8001", "AG8002", "AG8003", "AB1010", "AB1011"})

OK, EMPTY, FAILED, SKIPPED = "ok", "empty", "failed", "not requested"

Request = Callable[[Dict[str, str]], Awaitable[Any]]
Relogin = Callable[[], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


_ADDRESS = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b|\b(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\b")


def brief(exc: BaseException) -> str:
    """An exception as one short line for the record: what kind, and what it
    said — without any network address it happened to mention."""
    text = _ADDRESS.sub("<address>", " ".join(str(exc).split()))
    return "{}: {}".format(type(exc).__name__, text[:160]) if text else type(exc).__name__


def day_start(moment: datetime) -> datetime:
    """Midnight, exchange time, of the day this moment falls in. A history is
    asked for in whole exchange days: the venue stamps a daily bar at local
    midnight, so a request that begins mid-morning gets that day's hourly
    bars and not its daily one, and the two disagree about where it starts."""
    return moment.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0)


class HistoryUnavailable(RuntimeError):
    """The request could not even be made — an interval the API does not
    serve, a range that is not one, or a scrip master that would not load."""


@dataclass
class Window:
    start: str
    end: str
    status: str = SKIPPED
    rows: int = 0
    attempts: int = 0
    error: Optional[str] = None


@dataclass
class HistoryReport:
    """Where a history came from and whether all of it arrived."""
    exchange: str
    trading_symbol: str
    token: str
    timeframe: str
    interval: str
    requested_from: str
    requested_to: str
    broker: str = "angelone"
    received_from: Optional[float] = None
    received_to: Optional[float] = None
    rows: int = 0
    malformed_rows: int = 0
    retries: int = 0
    relogins: int = 0
    complete: bool = False
    incomplete_reason: Optional[str] = None
    stopped_early: Optional[str] = None
    retrieved_at: float = 0.0
    timezone: str = ("venue stamps carry +05:30 (Asia/Kolkata); stored as UTC epoch "
                     "seconds of the bar's open")
    source_version: str = SOURCE_VERSION
    windows: List[Window] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_time(stamp: Any) -> float:
    """A venue timestamp as UTC epoch seconds. No offset means exchange time."""
    parsed = datetime.fromisoformat(str(stamp))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.timestamp()


def parse_candles(candles: Any) -> Tuple[List[List[float]], int]:
    """Rows of [time, open, high, low, close, volume], and how many of the
    venue's rows could not be read. Those are counted, not hidden."""
    rows: List[List[float]] = []
    malformed = 0
    for candle in candles or []:
        try:
            rows.append([parse_time(candle[0]), float(candle[1]), float(candle[2]),
                         float(candle[3]), float(candle[4]), float(candle[5])])
        except (ValueError, IndexError, TypeError):
            malformed += 1
    return rows, malformed


def _windows(start: datetime, end: datetime, days: int) -> List[Tuple[datetime, datetime]]:
    """[start, end] cut into stretches of at most `days`, newest first."""
    out, upper = [], end
    while upper > start:
        lower = max(upper - timedelta(days=days), start)
        out.append((lower, upper))
        upper = lower
    return out


def _is_auth_failure(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    code = str(response.get("errorcode") or response.get("errorCode") or "").upper()
    message = str(response.get("message") or "").lower()
    return code in AUTH_ERROR_CODES or "invalid token" in message or "token expired" in message


def _describe(response: Any) -> str:
    if isinstance(response, dict):
        code = response.get("errorcode") or response.get("errorCode") or ""
        return "{} {}".format(code, response.get("message") or "no message").strip()
    return "unreadable response ({})".format(type(response).__name__)


async def _one_window(request: Request, params: Dict[str, str], window: Window,
                      report: HistoryReport, relogin: Optional[Relogin],
                      sleep: Sleep) -> List[List[float]]:
    """Ask for one window until it answers or the tries run out."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        window.attempts = attempt
        if attempt > 1:
            report.retries += 1
            await sleep(BACKOFF_SECONDS * 2 ** (attempt - 2))
        try:
            response = await request(params)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - every SDK and network failure is one case
            window.error = "request raised " + brief(exc)
            continue
        if isinstance(response, dict) and response.get("status"):
            rows, malformed = parse_candles(response.get("data"))
            report.malformed_rows += malformed
            window.rows, window.error = len(rows), None
            window.status = OK if rows else EMPTY
            return rows
        window.error = _describe(response)
        if _is_auth_failure(response) and relogin is not None and report.relogins < 2:
            report.relogins += 1
            try:
                await relogin()
            except Exception as exc:  # noqa: BLE001 - a failed login is this window's failure
                window.error = "session expired and login failed ({})".format(
                    type(exc).__name__)
    window.status = FAILED
    return []


def _verdict(report: HistoryReport) -> None:
    """Decide whether the whole of what was asked for arrived."""
    newest_first = report.windows
    failed = [w for w in newest_first if w.status == FAILED]
    skipped_after_failure = report.stopped_early is not None and "failed" in report.stopped_early
    statuses = [w.status for w in newest_first]
    have = [i for i, s in enumerate(statuses) if s == OK]
    reasons = []
    if failed:
        reasons.append("{} of {} requests failed ({})".format(
            len(failed), len(newest_first), failed[0].error or "no reason given"))
    if skipped_after_failure:
        reasons.append(report.stopped_early)
    if not have:
        reasons.append("no window returned any bars")
    else:
        # Newer than the newest data, or between two windows that had some.
        if EMPTY in statuses[:have[0]]:
            reasons.append("the most recent window came back empty")
        if EMPTY in statuses[have[0]:have[-1]]:
            reasons.append("an empty window sits between two that had bars")
    if report.rows and report.malformed_rows > 0.01 * (report.rows + report.malformed_rows):
        reasons.append("{} of the venue's rows could not be read".format(report.malformed_rows))
    report.complete = not reasons
    report.incomplete_reason = "; ".join(reasons) + "." if reasons else None


async def fetch_range(request: Request, *, exchange: str, trading_symbol: str, token: str,
                      timeframe: str, start: datetime, end: datetime,
                      relogin: Optional[Relogin] = None, sleep: Sleep = asyncio.sleep,
                      pause: float = PAUSE_SECONDS,
                      now: Optional[float] = None) -> Tuple[List[List[float]], HistoryReport]:
    """Every candle of `timeframe` between `start` and `end`, oldest first,
    with the report of how it was got. `request` is given SmartAPI's
    `getCandleData` parameters and returns its response.

    Raises `HistoryUnavailable` only for a request that cannot be formed. A
    request that fails is not an exception: it is a report that says so.
    """
    interval = INTERVALS.get(timeframe.lower())
    if interval is None:
        raise HistoryUnavailable(
            "Angel One serves no {} candles; ask for one of {}.".format(
                timeframe, ", ".join(INTERVALS)))
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise HistoryUnavailable("A history needs a start before its end, both with a time zone.")
    start, end = day_start(start), end.astimezone(IST)

    report = HistoryReport(
        exchange=exchange, trading_symbol=trading_symbol, token=str(token),
        timeframe=timeframe.lower(), interval=interval,
        requested_from=start.isoformat(timespec="minutes"),
        requested_to=end.isoformat(timespec="minutes"),
        retrieved_at=time.time() if now is None else float(now))
    spans = _windows(start, end, WINDOW_DAYS[interval])
    report.windows = [Window(lo.strftime("%Y-%m-%d %H:%M"), hi.strftime("%Y-%m-%d %H:%M"))
                      for lo, hi in spans]

    rows: Dict[float, List[float]] = {}
    failures = empties = 0
    for index, window in enumerate(report.windows):
        if index:
            await sleep(pause)
        params = {"exchange": exchange, "symboltoken": str(token), "interval": interval,
                  "fromdate": window.start, "todate": window.end}
        for row in await _one_window(request, params, window, report, relogin, sleep):
            rows[row[0]] = row
        failures = failures + 1 if window.status == FAILED else 0
        empties = empties + 1 if window.status == EMPTY else 0
        if failures >= MAX_CONSECUTIVE_FAILURES:
            report.stopped_early = "{} requests in a row failed; the rest were not made".format(
                failures)
            break
        if rows and empties >= LISTING_EMPTY_WINDOWS:
            report.stopped_early = ("{} empty windows in a row before the oldest bar — taken "
                                    "as before the listing".format(empties))
            break

    ordered = [rows[key] for key in sorted(rows)]
    report.rows = len(ordered)
    if ordered:
        report.received_from, report.received_to = ordered[0][0], ordered[-1][0]
    _verdict(report)
    if not report.complete:
        log.warning("Angel One history for %s %s is incomplete: %s", trading_symbol,
                    timeframe, report.incomplete_reason)
    return ordered, report


@dataclass(frozen=True)
class Scrip:
    """One row of Angel One's scrip master, as far as a history needs it."""
    exchange: str
    trading_symbol: str
    token: str
    name: str = ""
    instrument_type: str = ""


class AngelHistorySource:
    """The live source: Angel One's session and its scrip master. Reads only
    — it holds no method that reaches an order.

    `pause` is the wait between requests. The limit is shared with whatever
    else is using the same API key — the running application's charts among
    them — so a snapshot taken while the market is open should ask slowly.
    """

    name = "angelone"

    def __init__(self, pause: float = PAUSE_SECONDS) -> None:
        self.pause = max(float(pause), PAUSE_SECONDS)

    async def scrip(self, exchange: str, trading_symbol: str) -> Optional[Scrip]:
        """The scrip master's row for this exact trading symbol, or None. A
        token is looked up, never written down: it is what a history is
        actually asked for by, and a symbol can be given a new one."""
        from ..broker.angelone.instruments import angel_instruments

        try:
            await angel_instruments.ensure_loaded()
        except Exception as exc:  # noqa: BLE001 - a download of tens of MB fails many ways
            # Not "this symbol is not listed": nothing is known about any symbol.
            raise HistoryUnavailable(
                "Angel One's scrip master could not be loaded ({}), so no token can be "
                "looked up.".format(brief(exc))) from exc
        row = angel_instruments._by_key.get((exchange, trading_symbol))  # noqa: SLF001
        if row is None or not row.get("token"):
            return None
        return Scrip(exchange=exchange, trading_symbol=trading_symbol,
                     token=str(row["token"]), name=str(row.get("name") or ""),
                     instrument_type=str(row.get("instrumenttype") or ""))

    async def candles(self, scrip: Scrip, timeframe: str, start: datetime,
                      end: datetime) -> Tuple[List[List[float]], HistoryReport]:
        from ..broker.angelone.auth import angel_auth

        async def request(params: Dict[str, str]) -> Any:
            client = await asyncio.to_thread(angel_auth.ensure)
            return await asyncio.to_thread(client.getCandleData, params)

        async def relogin() -> None:
            await asyncio.to_thread(angel_auth.login)

        return await fetch_range(
            request, exchange=scrip.exchange, trading_symbol=scrip.trading_symbol,
            token=scrip.token, timeframe=timeframe, start=start, end=end, relogin=relogin,
            pause=self.pause)
