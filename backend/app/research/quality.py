"""Is this history fit to test on?

Two steps, kept apart so the record shows both what was wrong with the data
as it arrived and what was done about it:

* `clean` applies a fixed, short list of rules — sort, drop what cannot be a
  candle — and counts every row each rule removed. It never invents a bar: a
  gap stays a gap.
* `assess` measures what is left and decides whether it may be used.

A market with a session (`exchange_calendar.Session`) is held to that
session: its bars are checked against the session's grid rather than the
clock's, a full day is expected on every day it traded, and the days it
should have traded are taken from a measured calendar. Without a calendar a
missing day cannot be told from a holiday, and the history does not pass.

The rules carry a version. A result is only comparable with another that
was cleaned the same way.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from ..data.store import bar_open_time
from .exchange_calendar import DAY, Session, TradingCalendar

RULES_VERSION = "3"

#: Share of expected bars a market may be missing.
MAX_MISSING_PCT = 1.0
#: Share of the rows that arrived which may be thrown away as unusable.
MAX_DROPPED_PCT = 1.0
#: Fewer bars than this is not a history.
MIN_BARS = 300
#: Consecutive bars with no movement and no volume before the feed is
#: suspected of having gone stale.
STALE_RUN_BARS = 48
#: A bar's return this many robust deviations from the median is reported.
EXTREME_SIGMAS = 15.0
#: Share of an exchange-traded stock's bars that may show no volume. A bar
#: in which nothing traded is not a price anybody could have dealt at.
MAX_ZERO_VOLUME_PCT = 1.0
#: Trading days in a row a session market may be missing. One lost day costs
#: a strategy one bar; a fortnight is a hole it trades straight across, with
#: a stop that was never tested against anything that happened inside it —
#: and it stays well under one percent of an eight-year history.
MAX_MISSING_RUN_DAYS = 3
#: Share of days on which the session's last bar may be missing or empty
#: before the history says so out loud. Not a reason to refuse it: these are
#: a few bars in a thousand, well inside the limits above. But they are all
#: the *same* bar — the close — so a strategy that reads the last bar's price
#: or volume is wrong on every one of those days, while one that is out
#: before it is not touched. The history passes; the warning says which.
MAX_CLOSING_BAR_UNRELIABLE_PCT = 1.0
#: An overnight move this large, in a stock, is far more often a split or a
#: bonus the history was not adjusted for than a day's trading. Each one has
#: to be explained before the series is tested on.
SPLIT_LIKE_MOVE = 0.20

CLEANING_RULES = (
    "drop rows with a non-finite value",
    "sort by bar open time",
    "keep the last of rows sharing a timestamp",
    "drop bars whose time is not on the timeframe's grid",
    "drop bars with a non-positive price, high below low, or open/close outside high-low",
    "drop the bar still forming when the data was fetched",
)

#: What a market with a session is cleaned by. The grid is the session's,
#: and two rules are added.
SESSION_CLEANING_RULES = (
    "drop rows with a non-finite value",
    "stamp daily and weekly bars at the session open of their local day",
    "sort by bar open time",
    "keep the last of rows sharing a timestamp",
    "drop bars whose time is not on the session's grid for the timeframe",
    "drop bars that open outside the regular session",
    "drop bars with a non-positive price, high below low, or open/close outside high-low",
    "drop the bar still forming when the data was fetched",
)


def cleaning_rules(session: Optional[Session] = None) -> Tuple[str, ...]:
    return SESSION_CLEANING_RULES if session is not None else CLEANING_RULES


@dataclass
class QualityReport:
    rules_version: str = RULES_VERSION
    received: int = 0
    bars: int = 0
    first: float = 0.0
    last: float = 0.0
    #: A market that trades around the clock: every bar is expected.
    continuous: bool = True
    #: The exchange session the bars were held to, if the market has one.
    session: Optional[str] = None
    expected: Optional[int] = None
    missing: int = 0
    missing_pct: float = 0.0
    gaps: int = 0
    largest_gap_bars: int = 0
    non_finite_dropped: int = 0
    out_of_order: int = 0
    duplicates_dropped: int = 0
    misaligned_dropped: int = 0
    invalid_dropped: int = 0
    incomplete_dropped: int = 0
    zero_volume: int = 0
    zero_volume_pct: float = 0.0
    longest_stale_run: int = 0
    extreme_bars: int = 0
    # ── a market with a session ───────────────────────────────
    restamped: int = 0
    out_of_session_dropped: int = 0
    #: Days that had bars outside the regular session — a special sitting.
    out_of_session_dates: List[str] = field(default_factory=list)
    #: Calendar days this history has nothing for *inside* the session but
    #: did trade outside it: an evening sitting, not a missing day.
    special_session_days: int = 0
    #: Days with no bar where the session's last one should be; days where
    #: it is there, flat, and shows no volume; and the two as a share of
    #: all traded days. Intraday only.
    closing_bar_missing_days: int = 0
    closing_bar_empty_days: int = 0
    closing_bar_unreliable_pct: float = 0.0
    traded_days: int = 0
    #: Days with fewer bars than a full session has.
    short_days: int = 0
    #: Weekdays inside the history with no bars at all — holidays and
    #: missing days together, until a calendar separates them.
    absent_weekdays: int = 0
    #: Saturdays and Sundays that have bars: special sessions.
    weekend_days: int = 0
    #: Where the list of trading days came from; None if there was none.
    calendar: Optional[str] = None
    #: Days the calendar says the exchange was open and this history lacks.
    missing_days: int = 0
    missing_day_dates: List[str] = field(default_factory=list)
    #: The longest stretch of consecutive trading days it lacks, and where.
    longest_missing_run: int = 0
    longest_missing_run_from: Optional[str] = None
    #: Days this history has that the calendar does not.
    uncalendared_days: int = 0
    #: Overnight moves of `SPLIT_LIKE_MOVE` or more: [{"date", "ratio"}].
    split_like_moves: List[Dict[str, Any]] = field(default_factory=list)
    unexplained_moves: int = 0
    passed: bool = False
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _aligned(times: np.ndarray, tf_seconds: int) -> np.ndarray:
    if tf_seconds == 604_800:
        return np.array([bar_open_time(t, tf_seconds) == t for t in times], dtype=bool)
    return (times % tf_seconds) == 0


def clean(rows: Sequence[Sequence[float]], tf_seconds: int, now: Optional[float] = None,
          session: Optional[Session] = None) -> Tuple[np.ndarray, QualityReport]:
    """Apply the cleaning rules. Returns the usable bars and what was removed."""
    report = QualityReport(session=session.name if session else None)
    data = np.asarray(rows, dtype=float).reshape(-1, 6) if len(rows) else np.empty((0, 6))
    report.received = len(data)
    if not len(data):
        return data, report

    finite = np.isfinite(data).all(axis=1)
    report.non_finite_dropped = int((~finite).sum())
    data = data[finite]

    if session is not None and tf_seconds >= DAY and len(data):
        stamped = session.restamp(data[:, 0], tf_seconds)
        report.restamped = int((stamped != data[:, 0]).sum())
        data = data.copy()
        data[:, 0] = stamped

    report.out_of_order = int((np.diff(data[:, 0]) < 0).sum())
    data = data[np.argsort(data[:, 0], kind="stable")]

    # The last of a run of equal timestamps is the venue's final word on it.
    keep = np.r_[data[1:, 0] != data[:-1, 0], True] if len(data) else np.empty(0, dtype=bool)
    report.duplicates_dropped = int((~keep).sum())
    data = data[keep]

    aligned = (_aligned(data[:, 0], tf_seconds) if session is None
               else session.aligned(data[:, 0], tf_seconds))
    report.misaligned_dropped = int((~aligned).sum())
    data = data[aligned]

    if session is not None:
        inside = session.in_session(data[:, 0], tf_seconds)
        report.out_of_session_dropped = int((~inside).sum())
        report.out_of_session_dates = [
            session.iso(day) for day in np.unique(session.local_day(data[~inside, 0]))[:60]]
        data = data[inside]

    o, h, low, c, v = (data[:, i] for i in range(1, 6))
    valid = ((np.minimum.reduce([o, h, low, c]) > 0) & (h >= low)
             & (h >= np.maximum(o, c)) & (low <= np.minimum(o, c)) & (v >= 0))
    report.invalid_dropped = int((~valid).sum())
    data = data[valid]

    if now is not None and len(data):
        ends = (data[:, 0] + tf_seconds if session is None
                else session.bar_end(data[:, 0], tf_seconds))
        closed = ends <= now
        report.incomplete_dropped = int((~closed).sum())
        data = data[closed]
    return data, report


def _longest_run(mask: np.ndarray) -> int:
    best = run = 0
    for flag in mask:
        run = run + 1 if flag else 0
        best = max(best, run)
    return int(best)


def _assess_session(data: np.ndarray, tf_seconds: int, report: QualityReport,
                    session: Session, calendar: Optional[TradingCalendar],
                    explained: Iterable[str]) -> None:
    """What a session market's history is missing, by day and within a day,
    and which overnight moves nobody has accounted for."""
    times = data[:, 0]
    days = session.local_day(times)
    traded, counts = np.unique(days, return_counts=True)
    report.traded_days = int(len(traded))
    report.weekend_days = int((session.weekday(traded) >= 5).sum())

    per_day = session.bars_per_day(tf_seconds)
    expected = int(len(traded)) * per_day if tf_seconds <= DAY else int(len(data))
    if tf_seconds < DAY:
        report.short_days = int((counts < per_day).sum())
        same_day = days[1:] == days[:-1]
        steps = np.diff(times)[same_day] / tf_seconds
        holes = steps[steps > 1.5]
        report.gaps = int(len(holes))
        report.largest_gap_bars = int(round(holes.max() - 1)) if len(holes) else 0

    if tf_seconds <= DAY:
        span = np.arange(traded[0], traded[-1] + 1)
        weekdays = span[session.weekday(span) < 5]
        report.absent_weekdays = int(len(np.setdiff1d(weekdays, traded)))
        if calendar is not None:
            report.calendar = calendar.source
            should = calendar.within(int(traded[0]), int(traded[-1]))
            lacking = np.setdiff1d(should, traded)
            # A day the exchange only sat outside its regular hours — the
            # Diwali evening session — is in the calendar and has no bars
            # here because they were left out, not because they are missing.
            sat_outside = {day for day in lacking
                           if session.iso(day) in set(report.out_of_session_dates)}
            report.special_session_days = len(sat_outside)
            lacking = np.array([day for day in lacking if day not in sat_outside],
                               dtype=np.int64)
            should = np.array([day for day in should if day not in sat_outside],
                              dtype=np.int64)
            report.missing_days = int(len(lacking))
            report.missing_day_dates = [session.iso(d) for d in lacking[:25]]
            # Consecutive in the calendar's own count of days, so a weekend
            # between two missing days does not end the run.
            run, start, previous = 0, 0, None
            for position in np.flatnonzero(~np.isin(should, traded)):
                if previous is not None and position == previous + 1:
                    run += 1
                else:
                    run, start = 1, position
                previous = position
                if run > report.longest_missing_run:
                    report.longest_missing_run = run
                    report.longest_missing_run_from = session.iso(int(should[start]))
            covered = traded[(traded >= calendar.first) & (traded <= calendar.last)]
            report.uncalendared_days = int(len(np.setdiff1d(covered, should)))
            expected += report.missing_days * per_day
            if calendar.first > traded[0] or calendar.last < traded[-1]:
                report.problems.append(
                    "The trading calendar ({}) covers {} to {} and this history runs "
                    "{} to {} — the days outside it were not checked.".format(
                        calendar.source, session.iso(calendar.first),
                        session.iso(calendar.last), session.iso(int(traded[0])),
                        session.iso(int(traded[-1]))))
        else:
            report.problems.append(
                "Trading days were not checked against a calendar: {} weekdays inside "
                "this history have no bars, and a holiday cannot be told from a missing "
                "day.".format(report.absent_weekdays))

    report.expected = expected
    report.missing = max(expected - len(data), 0)
    report.missing_pct = round(100.0 * report.missing / expected, 3) if expected else 0.0

    if tf_seconds < DAY and per_day > 1:
        # The session's last bar, day by day: there, there but dead, or not.
        last_offset = (per_day - 1) * tf_seconds
        is_last = np.isclose(times - session.day_open(days), last_offset)
        dead = is_last & (data[:, 5] == 0) & (data[:, 2] == data[:, 3])
        report.closing_bar_missing_days = int(len(traded) - len(np.unique(days[is_last])))
        report.closing_bar_empty_days = int(len(np.unique(days[dead])))
        report.closing_bar_unreliable_pct = round(
            100.0 * (report.closing_bar_missing_days + report.closing_bar_empty_days)
            / len(traded), 3)

    # The first bar of each day against the last bar of the day before.
    opens_day = np.r_[False, days[1:] != days[:-1]]
    if opens_day.any():
        index = np.flatnonzero(opens_day)
        ratio = data[index, 1] / data[index - 1, 4]
        big = (ratio >= 1.0 + SPLIT_LIKE_MOVE) | (ratio <= 1.0 / (1.0 + SPLIT_LIKE_MOVE))
        explained = set(explained)
        for i, r in zip(index[big], ratio[big]):
            date = session.iso(int(days[i]))
            report.split_like_moves.append({"date": date, "ratio": round(float(r), 4),
                                            "explained": date in explained})
        report.unexplained_moves = sum(1 for m in report.split_like_moves if not m["explained"])


def assess(data: np.ndarray, tf_seconds: int, report: Optional[QualityReport] = None,
           continuous: bool = True, min_bars: int = MIN_BARS, *,
           session: Optional[Session] = None, calendar: Optional[TradingCalendar] = None,
           explained_moves: Iterable[str] = ()) -> QualityReport:
    """Measure cleaned bars and decide whether they may be tested on."""
    report = report or QualityReport(received=len(data))
    if session is not None:
        continuous, report.session = False, session.name
    report.continuous = continuous
    report.bars = len(data)
    if not len(data):
        report.problems.append("No usable bars.")
        return report

    times, close, volume = data[:, 0], data[:, 4], data[:, 5]
    report.first, report.last = float(times[0]), float(times[-1])
    steps = np.diff(times) / tf_seconds
    if continuous:
        report.expected = int(round((times[-1] - times[0]) / tf_seconds)) + 1
        report.missing = max(report.expected - len(data), 0)
        report.missing_pct = round(100.0 * report.missing / report.expected, 3)
        holes = steps[steps > 1.5]
        report.gaps = int(len(holes))
        report.largest_gap_bars = int(round(holes.max() - 1)) if len(holes) else 0
    elif session is not None:
        _assess_session(data, tf_seconds, report, session, calendar, explained_moves)

    report.zero_volume = int((volume == 0).sum())
    report.zero_volume_pct = round(100.0 * report.zero_volume / len(data), 3)
    flat = (data[:, 2] == data[:, 3]) & (volume == 0)
    report.longest_stale_run = _longest_run(flat)

    returns = np.diff(np.log(close))
    if len(returns) > 30:
        centre = np.median(returns)
        scale = 1.4826 * np.median(np.abs(returns - centre))
        if scale > 0:
            report.extreme_bars = int((np.abs(returns - centre) > EXTREME_SIGMAS * scale).sum())

    dropped = (report.non_finite_dropped + report.misaligned_dropped
               + report.invalid_dropped + report.duplicates_dropped)
    dropped_pct = 100.0 * dropped / max(report.received, 1)

    if len(data) < min_bars:
        report.problems.append("Only {} usable bars (need {}).".format(len(data), min_bars))
    if dropped_pct > MAX_DROPPED_PCT:
        report.problems.append(
            "{:.2f}% of the rows received were unusable (limit {}%).".format(
                dropped_pct, MAX_DROPPED_PCT))
    if continuous and report.missing_pct > MAX_MISSING_PCT:
        report.problems.append(
            "{:.2f}% of the bars this market should have are missing (limit {}%)."
            .format(report.missing_pct, MAX_MISSING_PCT))
    if session is not None and report.missing_pct > MAX_MISSING_PCT:
        report.problems.append(
            "{:.2f}% of the bars this market's sessions should have are missing "
            "(limit {}%): {} whole days and {} short days.".format(
                report.missing_pct, MAX_MISSING_PCT, report.missing_days, report.short_days))
    if report.longest_missing_run > MAX_MISSING_RUN_DAYS:
        report.problems.append(
            "{} trading days in a row are missing from {} (limit {}) — a strategy "
            "would trade straight across that hole.".format(
                report.longest_missing_run, report.longest_missing_run_from,
                MAX_MISSING_RUN_DAYS))
    if session is not None and report.zero_volume_pct > MAX_ZERO_VOLUME_PCT:
        report.problems.append(
            "{:.2f}% of the bars show no volume (limit {}%) — nothing traded at those "
            "prices.".format(report.zero_volume_pct, MAX_ZERO_VOLUME_PCT))
    if report.unexplained_moves:
        moves = [m for m in report.split_like_moves if not m["explained"]]
        report.problems.append(
            "{} overnight move(s) of {:.0f}% or more that nobody has accounted for "
            "({}) — a split or bonus the history was not adjusted for looks exactly "
            "like this.".format(
                len(moves), 100 * SPLIT_LIKE_MOVE,
                ", ".join("{} ×{}".format(m["date"], m["ratio"]) for m in moves[:6])))
    if report.longest_stale_run > STALE_RUN_BARS:
        report.problems.append(
            "{} consecutive bars with no movement and no volume — the feed looks "
            "stale there.".format(report.longest_stale_run))

    if report.out_of_order:
        report.warnings.append("{} rows arrived out of order.".format(report.out_of_order))
    if report.largest_gap_bars > 3:
        report.warnings.append(
            "Largest gap is {} bars; {} gaps in all.".format(
                report.largest_gap_bars, report.gaps))
    if report.out_of_session_dropped:
        report.warnings.append(
            "{} bars opened outside the regular session and were left out.".format(
                report.out_of_session_dropped))
    if report.closing_bar_unreliable_pct > MAX_CLOSING_BAR_UNRELIABLE_PCT:
        report.warnings.append(
            "The session's last bar is missing on {} days and empty on {} ({:.1f}% of "
            "days): its close and its volume are not to be relied on, and a strategy "
            "tested on this history has to be out before it.".format(
                report.closing_bar_missing_days, report.closing_bar_empty_days,
                report.closing_bar_unreliable_pct))
    if report.special_session_days:
        report.warnings.append(
            "{} day(s) traded only outside the regular session and are not counted "
            "as missing.".format(report.special_session_days))
    if report.uncalendared_days:
        report.warnings.append(
            "{} traded day(s) are not in the calendar — a special session, or a "
            "calendar that is missing a day.".format(report.uncalendared_days))
    if report.extreme_bars:
        report.warnings.append(
            "{} bars moved more than {:g} robust deviations — check them before "
            "trusting a result that depends on them.".format(
                report.extreme_bars, EXTREME_SIGMAS))
    report.passed = not report.problems
    return report


def validate(rows: Sequence[Sequence[float]], tf_seconds: int, *, continuous: bool = True,
             now: Optional[float] = None, min_bars: int = MIN_BARS,
             session: Optional[Session] = None, calendar: Optional[TradingCalendar] = None,
             explained_moves: Iterable[str] = ()) -> Tuple[np.ndarray, QualityReport]:
    """Clean, then assess. The one call a dataset is built with."""
    data, report = clean(rows, tf_seconds, now, session)
    return data, assess(data, tf_seconds, report, continuous, min_bars, session=session,
                        calendar=calendar, explained_moves=explained_moves)
