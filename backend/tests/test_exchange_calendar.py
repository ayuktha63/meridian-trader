"""A market with a session is held to that session: its own grid, a full day
on every day it traded, and the days it should have traded."""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pytest

from app.research import datasets, quality
from app.research.exchange_calendar import (
    DAY, NSE_EQUITY, Session, calendar_from_times, calendar_from_universe,
)

from india_fixtures import daily_rows, intraday_rows, ist, on, weekdays, without

HOUR, QUARTER = 3600, 900
START = date(2024, 1, 1)                       # a Monday
DIWALI = date(2024, 11, 1)


def calendar_of(days):
    return calendar_from_times(NSE_EQUITY, [ist(d.year, d.month, d.day) for d in days], "test")


# ── the grid ──────────────────────────────────────────────────

def test_a_session_has_the_bars_the_exchange_prints():
    assert [NSE_EQUITY.bars_per_day(tf) for tf in (60, 300, 900, 1800, 3600, DAY)] == [
        375, 75, 25, 13, 7, 1]


def test_hourly_bars_open_on_the_quarter_and_a_clock_grid_throws_them_all_away():
    """The defect this module exists for: NSE's hourly bars open at 09:15,
    10:15 … — never on a UTC hour — and the daily bar is stamped at local
    midnight. Checked against the clock's grid, every one of them is dropped."""
    days = weekdays(START, 60)
    hourly, daily = intraday_rows(days, 60), daily_rows(days)

    kept, report = quality.validate(hourly, HOUR, continuous=False)
    assert len(kept) == 0 and report.misaligned_dropped == len(hourly)
    kept, report = quality.validate(daily, DAY, continuous=False)
    assert len(kept) == 0 and report.misaligned_dropped == len(daily)

    calendar = calendar_of(days)
    kept, report = quality.validate(hourly, HOUR, session=NSE_EQUITY, calendar=calendar)
    assert len(kept) == len(hourly) == 60 * 7 and report.misaligned_dropped == 0
    kept, report = quality.validate(daily, DAY, session=NSE_EQUITY, calendar=calendar,
                                    min_bars=50)
    assert len(kept) == 60 and report.misaligned_dropped == 0 and report.passed


def test_a_daily_bar_is_stamped_at_the_open_of_its_own_day():
    """Local midnight in India is the evening before in UTC. Left there, the
    first bar after a date cut lands on the wrong side of it."""
    monday = date(2024, 7, 1)
    kept, report = quality.validate(daily_rows([monday]), DAY, session=NSE_EQUITY, min_bars=1,
                                    calendar=calendar_of([monday]))
    opened = datetime.fromtimestamp(kept[0, 0], tz=timezone.utc)
    assert report.restamped == 1
    assert (opened.date(), opened.hour, opened.minute) == (monday, 3, 45)
    assert kept[0, 0] >= datetime(2024, 7, 1, tzinfo=timezone.utc).timestamp()


def test_the_last_bar_of_the_day_is_complete_at_the_close_not_an_hour_later():
    day = date(2024, 7, 1)
    rows = intraday_rows([day], 60)
    assert rows[-1][0] == ist(2024, 7, 1, 15, 15)
    before, _ = quality.clean(rows, HOUR, now=ist(2024, 7, 1, 15, 29), session=NSE_EQUITY)
    at_close, _ = quality.clean(rows, HOUR, now=ist(2024, 7, 1, 15, 30), session=NSE_EQUITY)
    assert len(before) == 6 and len(at_close) == 7
    assert NSE_EQUITY.bar_end([rows[0][0]], HOUR)[0] == ist(2024, 7, 1, 10, 15)


def test_an_evening_sitting_is_left_out_and_said_so():
    days = weekdays(START, 30)
    rows = intraday_rows(days, 15)
    muhurat = [ist(2024, 1, 15, 18, 15) + i * QUARTER for i in range(4)]
    rows += [[t, 1000.0, 1001.0, 999.0, 1000.5, 50.0] for t in muhurat]
    kept, report = quality.validate(rows, QUARTER, session=NSE_EQUITY, calendar=calendar_of(days))
    assert report.out_of_session_dropped == 4 and len(kept) == 30 * 25
    assert report.passed, report.problems
    assert any("outside the regular session" in w for w in report.warnings)


def test_a_bar_off_the_sessions_grid_is_dropped():
    days = weekdays(START, 30)
    rows = intraday_rows(days, 15)
    rows.append([ist(2024, 1, 2, 9, 20), 1000.0, 1001.0, 999.0, 1000.5, 50.0])
    _, report = quality.validate(rows, QUARTER, session=NSE_EQUITY, calendar=calendar_of(days))
    assert report.misaligned_dropped == 1


# ── what is missing ───────────────────────────────────────────

def test_a_holiday_is_not_a_missing_day_and_a_missing_day_is():
    days = weekdays(START, 400, holidays=[DIWALI])
    calendar = calendar_of(days)
    rows = daily_rows(days)

    _, clean = quality.validate(rows, DAY, session=NSE_EQUITY, calendar=calendar)
    assert clean.passed and clean.missing_days == 0 and clean.absent_weekdays == 1

    lost = date(2024, 6, 12)
    _, holed = quality.validate(without(rows, on(rows, lost)), DAY, session=NSE_EQUITY,
                                calendar=calendar)
    assert holed.missing_days == 1 and holed.missing_day_dates == ["2024-06-12"]
    assert holed.missing == 1 and holed.passed            # one day in 400 is under the limit


def test_too_many_missing_days_fail_the_history():
    days = weekdays(START, 400)
    rows = daily_rows(days)
    gone = [i for day in days[100:106] for i in on(rows, day)]
    _, report = quality.validate(without(rows, gone), DAY, session=NSE_EQUITY,
                                 calendar=calendar_of(days))
    assert report.missing_days == 6 and report.missing_pct == 1.5 and not report.passed
    assert any("sessions should have are missing" in p for p in report.problems)


def test_a_morning_without_bars_is_counted_inside_the_day():
    days = weekdays(START, 40)
    rows = intraday_rows(days, 15)
    morning = on(rows, days[5])[:10]
    _, report = quality.validate(without(rows, morning), QUARTER, session=NSE_EQUITY,
                                 calendar=calendar_of(days))
    assert report.short_days == 1 and report.missing == 10
    assert report.expected == 40 * 25 and report.missing_pct == 1.0


def test_a_hole_inside_a_day_is_a_gap_and_the_night_is_not():
    days = weekdays(START, 40)
    rows = intraday_rows(days, 15)
    middle = on(rows, days[3])[8:13]
    _, report = quality.validate(without(rows, middle), QUARTER, session=NSE_EQUITY,
                                 calendar=calendar_of(days))
    assert report.gaps == 1 and report.largest_gap_bars == 5


def test_without_a_calendar_a_session_history_does_not_pass():
    """A weekday with no bars is a holiday or a hole, and nothing in the
    series says which."""
    days = weekdays(START, 400, holidays=[DIWALI])
    _, report = quality.validate(daily_rows(days), DAY, session=NSE_EQUITY)
    assert not report.passed and report.calendar is None
    assert any("not checked against a calendar" in p for p in report.problems)


def test_a_calendar_that_does_not_cover_the_history_does_not_vouch_for_it():
    days = weekdays(START, 400)
    _, report = quality.validate(daily_rows(days), DAY, session=NSE_EQUITY,
                                 calendar=calendar_of(days[50:]))
    assert not report.passed
    assert any("days outside it were not checked" in p for p in report.problems)


def test_bars_in_which_nothing_traded_fail_a_stock():
    days = weekdays(START, 400)
    rows = daily_rows(days)
    for row in rows[:8]:
        row[5] = 0.0
    _, report = quality.validate(rows, DAY, session=NSE_EQUITY, calendar=calendar_of(days))
    assert report.zero_volume_pct == 2.0 and not report.passed
    assert any("show no volume" in p for p in report.problems)


def test_a_saturday_session_is_a_traded_day_not_an_error():
    days = weekdays(START, 320) + [date(2025, 2, 1)]          # budget day, a Saturday
    days.sort()
    _, report = quality.validate(daily_rows(days), DAY, session=NSE_EQUITY,
                                 calendar=calendar_of(days))
    assert report.weekend_days == 1 and report.passed


# ── corporate actions ─────────────────────────────────────────

def split(rows, at, ratio):
    """An unadjusted split: every price from `at` on divided by `ratio`."""
    out = [list(r) for r in rows]
    for row in out[at:]:
        for i in (1, 2, 3, 4):
            row[i] /= ratio
        row[5] *= ratio
    return out


def test_an_unadjusted_split_fails_the_series_until_somebody_accounts_for_it():
    days = weekdays(START, 400)
    calendar = calendar_of(days)
    rows = split(daily_rows(days), 200, 2.0)
    when = days[200].isoformat()

    _, report = quality.validate(rows, DAY, session=NSE_EQUITY, calendar=calendar)
    assert not report.passed and report.unexplained_moves == 1
    assert report.split_like_moves[0]["date"] == when
    assert 0.45 < report.split_like_moves[0]["ratio"] < 0.55
    assert any("split or bonus" in p for p in report.problems)

    _, explained = quality.validate(rows, DAY, session=NSE_EQUITY, calendar=calendar,
                                    explained_moves=[when])
    assert explained.passed and explained.unexplained_moves == 0
    assert explained.split_like_moves[0]["explained"] is True


def test_the_same_split_is_caught_on_intraday_bars_at_the_open():
    days = weekdays(START, 40)
    rows = split(intraday_rows(days, 15), 20 * 25, 5.0)
    _, report = quality.validate(rows, QUARTER, session=NSE_EQUITY, calendar=calendar_of(days))
    assert report.unexplained_moves == 1
    assert report.split_like_moves[0]["date"] == days[20].isoformat()


def test_an_ordinary_bad_day_is_not_called_a_split():
    days = weekdays(START, 400)
    rows = daily_rows(days)
    for row in rows[200:]:
        for i in (1, 2, 3, 4):
            row[i] *= 0.88                       # a 12% gap down on results
    _, report = quality.validate(rows, DAY, session=NSE_EQUITY, calendar=calendar_of(days))
    assert report.split_like_moves == [] and report.passed


# ── the calendar ──────────────────────────────────────────────

def test_a_universe_calendar_is_what_most_of_it_agrees_on():
    days = weekdays(START, 120, holidays=[date(2024, 3, 25)])
    full = [ist(d.year, d.month, d.day) for d in days]
    one_absent = [t for t in full if t != ist(2024, 2, 14)]
    odd_day = full + [ist(2024, 3, 25)]            # one stock "trades" on the holiday
    calendar = calendar_from_universe(NSE_EQUITY, [full, full, one_absent, odd_day], "test")
    known = {Session.iso(d) for d in calendar.days}
    assert "2024-02-14" in known                    # three of four traded: the day was open
    assert "2024-03-25" not in known                # one of four: it was not
    assert len(calendar.days) == 120


def test_a_universe_calendar_only_covers_where_enough_stocks_were_listed():
    days = weekdays(START, 120)
    full = [ist(d.year, d.month, d.day) for d in days]
    calendar = calendar_from_universe(NSE_EQUITY, [full, full[60:], full[60:]], "test")
    assert Session.iso(calendar.first) == days[60].isoformat()
    with pytest.raises(ValueError, match="at least 3 series"):
        calendar_from_universe(NSE_EQUITY, [full, full], "test")


# ── snapshots ─────────────────────────────────────────────────

def test_a_snapshot_records_its_session_its_calendar_and_where_it_came_from():
    days = weekdays(START, 400)
    provenance = {"broker": "angelone", "exchange": "NSE", "trading_symbol": "RELIANCE-EQ",
                  "token": "2885", "interval": "ONE_DAY", "complete": True}
    built = datasets.build("angelone", "RELIANCE", "1d", daily_rows(days), session=NSE_EQUITY,
                           calendar=calendar_of(days), provenance=provenance)
    assert built.quality_passed and built.complete and built.session is NSE_EQUITY
    assert built.meta["session"]["open"] == "09:15" and built.meta["session"]["close"] == "15:30"
    assert built.meta["calendar"]["days"] == 400
    assert "stamp daily and weekly bars" in " ".join(built.meta["cleaning_rules"])
    described = built.describe()
    assert described["session"] == "NSE_EQ" and described["source"]["token"] == "2885"

    datasets.save(built)
    again = datasets.load(built.id)
    assert again.session is NSE_EQUITY and again.provenance["token"] == "2885"
    assert np.array_equal(again.rows, built.rows)


def test_an_incomplete_history_is_kept_and_does_not_pass():
    days = weekdays(START, 400)
    provenance = {"broker": "angelone", "complete": False,
                  "incomplete_reason": "1 of 5 requests failed (AB1004)."}
    built = datasets.build("angelone", "SBIN", "1d", daily_rows(days), session=NSE_EQUITY,
                           calendar=calendar_of(days), provenance=provenance)
    assert built.bars == 400 and not built.complete and not built.quality_passed
    assert built.meta["quality"]["problems"][0].startswith("The history is incomplete: 1 of 5")
    assert built.describe()["complete"] is False


def test_a_day_is_built_from_a_whole_session_or_not_at_all():
    days = weekdays(START, 320)
    calendar = calendar_of(days)
    rows = intraday_rows(days, 15)
    rows = without(rows, on(rows, days[7])[20:])          # an afternoon lost
    quarter = datasets.build("angelone", "INFY", "15m", rows, session=NSE_EQUITY,
                             calendar=calendar)
    daily = datasets.resample(quarter, "1d", calendar=calendar)

    assert daily.bars == 319 and daily.meta["derived_from"] == quarter.id
    assert daily.meta["quality"]["missing_day_dates"] == [days[7].isoformat()]
    first = [r for r in quarter.rows if NSE_EQUITY.local_day([r[0]])[0]
             == NSE_EQUITY.local_day([daily.rows[0, 0]])[0]]
    assert len(first) == 25
    assert daily.rows[0, 1] == first[0][1] and daily.rows[0, 4] == first[-1][4]
    assert daily.rows[0, 2] == max(r[2] for r in first)
    assert daily.rows[0, 3] == min(r[3] for r in first)
    assert daily.rows[0, 5] == pytest.approx(sum(r[5] for r in first))


def test_hours_are_built_on_the_sessions_grid_and_a_day_is_not_built_from_hours():
    days = weekdays(START, 60)
    calendar = calendar_of(days)
    quarter = datasets.build("angelone", "INFY", "15m", intraday_rows(days, 15),
                             session=NSE_EQUITY, calendar=calendar)
    hourly = datasets.resample(quarter, "1h", calendar=calendar)
    assert hourly.bars == 60 * 7 and hourly.quality_passed
    assert hourly.rows[0, 0] == ist(2024, 1, 1, 9, 15)
    assert hourly.rows[6, 0] == ist(2024, 1, 1, 15, 15)       # one quarter-hour long
    assert hourly.rows[6, 5] == quarter.rows[24, 5]

    with pytest.raises(datasets.DatasetError, match="not a whole number"):
        datasets.resample(hourly, "1d", calendar=calendar)
    with pytest.raises(datasets.DatasetError, match="Cannot build"):
        datasets.resample(hourly, "15m")


def test_weeks_are_built_from_days_and_start_on_monday():
    days = weekdays(START, 400, holidays=[date(2024, 1, 26)])
    calendar = calendar_of(days)
    daily = datasets.build("angelone", "TCS", "1d", daily_rows(days), session=NSE_EQUITY,
                           calendar=calendar)
    weekly = datasets.resample(daily, "1w")
    assert weekly.rows[0, 0] == ist(2024, 1, 1, 9, 15)
    assert weekly.rows[3, 0] == ist(2024, 1, 22, 9, 15)        # the week with the holiday
    assert weekly.rows[3, 5] == pytest.approx(daily.rows[15:19, 5].sum())


def test_the_same_bars_judged_differently_are_a_different_snapshot():
    """A snapshot is never rewritten. If its name were its bars alone, the
    first verdict on them would be the one on file for ever: explain a move,
    correct a calendar, and the stored snapshot would still say "failed"."""
    days = weekdays(START, 400)
    calendar = calendar_of(days)
    rows = split(daily_rows(days), 200, 2.0)
    source = {"broker": "angelone", "exchange": "NSE", "token": "2885", "complete": True}

    def build(**kwargs):
        options = dict(session=NSE_EQUITY, calendar=calendar, provenance=source)
        options.update(kwargs)
        return datasets.build("angelone", "RELIANCE", "1d", rows, **options)

    failed = build()
    datasets.save(failed)
    explained = build(explained_moves=[days[200].isoformat()])
    datasets.save(explained)

    assert np.array_equal(failed.rows, explained.rows) and failed.id != explained.id
    assert not datasets.load(failed.id).quality_passed
    assert datasets.load(explained.id).quality_passed

    assert build().id == failed.id                                  # same judgement, same name
    assert build(calendar=calendar_of(days + [date(2026, 1, 5)])).id != failed.id
    assert build(provenance={**source, "token": "99999"}).id != failed.id
    assert build(provenance={**source, "complete": False}).id != failed.id


def test_a_snapshot_with_no_session_is_named_as_it_always_was():
    import hashlib

    rows = [[1_700_000_000 // 3600 * 3600 + i * 3600, 100.0, 101.0, 99.0, 100.5, 10.0]
            for i in range(400)]
    built = datasets.build("angelone", "RELIANCE", "1h", rows)
    old_name = hashlib.sha256(
        b"meridian-dataset-v1|angelone|RELIANCE|1h|"
        + np.ascontiguousarray(built.rows, dtype="<f8").tobytes()).hexdigest()
    assert built.meta["sha256"] == old_name and built.meta["context"] is None


def test_a_long_hole_fails_a_history_that_is_under_the_missing_limit():
    """Ten trading days gone from eight years is half a percent — under the
    limit — and a strategy would trade straight across it. Seen in Angel
    One's daily series for Tata Motors, November 2021."""
    days = weekdays(START, 2000)
    calendar = calendar_of(days)
    rows = daily_rows(days)
    hole = days[700:710]
    assert hole[0].weekday() == 4 or any(d.weekday() == 0 for d in hole)   # spans a weekend

    _, holed = quality.validate(without(rows, [i for d in hole for i in on(rows, d)]), DAY,
                                session=NSE_EQUITY, calendar=calendar)
    assert holed.missing_pct == 0.5 and holed.longest_missing_run == 10
    assert holed.longest_missing_run_from == hole[0].isoformat() and not holed.passed
    assert any("in a row are missing" in p for p in holed.problems)

    scattered = [i for d in days[100:2000:200] for i in on(rows, d)]       # ten single days
    _, dotted = quality.validate(without(rows, scattered), DAY, session=NSE_EQUITY,
                                 calendar=calendar)
    assert dotted.missing_days == 10 and dotted.longest_missing_run == 1 and dotted.passed

    three = [i for d in days[300:303] for i in on(rows, d)]
    _, short = quality.validate(without(rows, three), DAY, session=NSE_EQUITY, calendar=calendar)
    assert short.longest_missing_run == 3 and short.passed


# ── the session's last bar ────────────────────────────────────

def test_a_dead_or_missing_closing_bar_is_a_warning_naming_the_bar_not_a_rejection():
    """Angel One's 15-minute history lacks the 15:15 bar on thirteen
    consecutive sessions in one stretch, and shows it flat with no volume on
    a handful more — always the same bar. A few in a thousand: inside every
    limit. But a strategy that reads the close is wrong on exactly those
    days, so the report says so."""
    days = weekdays(START, 120)
    rows = intraday_rows(days, 15, seed=2)
    lost = [i for d in days[40:53] for i in on(rows, d)[-1:]]            # 13 days lose 15:15
    rows = without(rows, lost)
    for d in days[80:84]:                                                # 4 more show it dead
        last = on(rows, d)[-1]
        rows[last][2] = rows[last][3] = rows[last][1] = rows[last][4]
        rows[last][5] = 0.0
    _, report = quality.validate(rows, QUARTER, session=NSE_EQUITY, calendar=calendar_of(days))
    assert report.closing_bar_missing_days == 13 and report.closing_bar_empty_days == 4
    assert report.closing_bar_unreliable_pct == pytest.approx(100 * 17 / 120, abs=0.01)
    assert report.short_days == 13 and report.passed, report.problems
    assert any("session's last bar is missing on 13 days and empty on 4" in w
               for w in report.warnings)


def test_a_clean_closing_bar_is_not_mentioned():
    days = weekdays(START, 120)
    _, report = quality.validate(intraday_rows(days, 15), QUARTER, session=NSE_EQUITY,
                                 calendar=calendar_of(days))
    assert report.closing_bar_unreliable_pct == 0.0 and report.warnings == []


def test_the_closing_bar_is_not_measured_on_daily_bars():
    days = weekdays(START, 400)
    _, report = quality.validate(daily_rows(days), DAY, session=NSE_EQUITY,
                                 calendar=calendar_of(days))
    assert report.closing_bar_missing_days == report.closing_bar_empty_days == 0


def test_a_day_the_exchange_sat_only_in_the_evening_is_not_a_missing_day():
    """Diwali's Saturday session, 14 Nov 2020, is in the index's calendar and
    has no bars inside regular hours. It was the commonest 'missing' day in
    the daily data (32 stocks); its bars are left out as out-of-session, and
    the day is not counted missing as well."""
    days = weekdays(START, 120)
    sitting = date(2024, 3, 16)                                         # a Saturday
    calendar = calendar_of(days + [sitting])
    rows = intraday_rows(days, 15)
    evening = [[ist(2024, 3, 16, 18, 15) + i * QUARTER, 100.0, 101.0, 99.0, 100.0, 50.0]
               for i in range(4)]
    _, report = quality.validate(rows + evening, QUARTER, session=NSE_EQUITY, calendar=calendar)
    assert report.special_session_days == 1 and report.missing_days == 0
    assert report.out_of_session_dates == ["2024-03-16"] and report.passed
    assert any("traded only outside the regular session" in w for w in report.warnings)

    # The same Saturday in the calendar with nothing at all that day is a gap.
    _, genuine = quality.validate(rows, QUARTER, session=NSE_EQUITY, calendar=calendar)
    assert genuine.missing_days == 1 and genuine.special_session_days == 0
