"""Angel One history for research: every request accounted for, and a
universe whose snapshots are checked before a study may load them."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.data import angel_history
from app.data.angel_history import (
    EMPTY, FAILED, IST, OK, SKIPPED, HistoryUnavailable, fetch_range, parse_candles, parse_time,
)
from app.research import datasets, india
from app.research.exchange_calendar import NSE_EQUITY, calendar_from_times

from india_fixtures import FakeBroker, daily_rows, ist, on, weekdays, without

APP = Path(__file__).resolve().parent.parent / "app"
START = datetime(2020, 1, 1, tzinfo=IST)
END = datetime(2024, 1, 1, tzinfo=IST)          # 1,461 days: three daily windows


TRIES = angel_history.MAX_ATTEMPTS


async def no_sleep(_seconds):
    return None


def venue(rows):
    """Rows as SmartAPI returns them: ISO stamps with the exchange's offset."""
    return [[datetime.fromtimestamp(r[0], tz=IST).isoformat(), *r[1:]] for r in rows]


class Api:
    """`getCandleData`, scripted: a list of answers per window, newest first."""

    def __init__(self, rows, script=None):
        self.rows, self.script, self.calls = rows, script or {}, []

    async def __call__(self, params):
        self.calls.append(params)
        window = len({c["fromdate"] for c in self.calls}) - 1
        answers = self.script.get(window)
        if answers:
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            if answer is not None:
                return answer
        lo = datetime.strptime(params["fromdate"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        hi = datetime.strptime(params["todate"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        inside = [r for r in self.rows if lo.timestamp() <= r[0] <= hi.timestamp()]
        return {"status": True, "message": "SUCCESS", "data": venue(inside)}


def fetch(api, **kwargs):
    options = dict(exchange="NSE", trading_symbol="RELIANCE-EQ", token="2885", timeframe="1d",
                   start=START, end=END, sleep=no_sleep, now=1_700_000_000.0)
    options.update(kwargs)
    return asyncio.run(fetch_range(api, **options))


def history(count=1000):
    return daily_rows(weekdays(date(2020, 1, 1), count))


REFUSED = {"status": False, "message": "Something Went Wrong, Please Try After Sometime",
           "errorcode": "AB1004"}
EXPIRED = {"status": False, "message": "Token Expired", "errorcode": "AG8002"}
NOTHING = {"status": True, "message": "SUCCESS", "data": []}


# ── a history that arrives whole ──────────────────────────────

def test_every_window_is_asked_for_and_the_bars_come_back_once_each_in_order():
    rows = history()
    got, report = fetch(Api(rows))
    assert [r[0] for r in got] == [r[0] for r in rows]
    assert report.complete and report.incomplete_reason is None
    assert [w.status for w in report.windows] == [OK, OK, OK]
    assert report.rows == 1000 and report.received_from == rows[0][0]
    assert (report.exchange, report.token, report.interval) == ("NSE", "2885", "ONE_DAY")
    assert report.requested_from.startswith("2020-01-01") and report.retrieved_at == 1_700_000_000.0


def test_no_window_asks_for_more_days_than_the_interval_allows():
    api = Api(history())
    fetch(api)
    for call in api.calls:
        span = (datetime.strptime(call["todate"], "%Y-%m-%d %H:%M")
                - datetime.strptime(call["fromdate"], "%Y-%m-%d %H:%M"))
        assert span <= timedelta(days=angel_history.WINDOW_DAYS["ONE_DAY"])
    assert api.calls[0]["todate"].startswith("2024-01-01")       # newest first
    assert {c["symboltoken"] for c in api.calls} == {"2885"}


def test_requests_are_paced_inside_the_rate_limit():
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    fetch(Api(history()), sleep=sleep)
    assert waits == [angel_history.PAUSE_SECONDS] * 2
    assert 1.0 / angel_history.PAUSE_SECONDS < 3.0              # three a second is the limit


# ── a history that does not ───────────────────────────────────

def test_a_request_that_keeps_failing_makes_the_history_incomplete_not_shorter():
    """The chart keeps what it has when a request fails. Here the bars that
    did arrive are returned with a report that says the rest did not."""
    rows = history()
    got, report = fetch(Api(rows, {1: [REFUSED] * TRIES}))
    assert 0 < len(got) < len(rows)
    assert not report.complete
    assert "1 of 3 requests failed" in report.incomplete_reason
    assert "AB1004" in report.incomplete_reason
    assert report.windows[1].status == FAILED and report.windows[1].attempts == TRIES
    assert report.retries == TRIES - 1


def test_a_request_that_fails_once_is_tried_again_and_the_history_is_whole():
    rows = history()
    got, report = fetch(Api(rows, {0: [ConnectionError("reset"), REFUSED]}))
    assert len(got) == len(rows) and report.complete
    assert report.windows[0].attempts == 3 and report.retries == 2


def test_a_refused_request_is_given_time_before_it_is_asked_again():
    """The rate limit is per minute as well as per second, and shared with
    the running application. Seen live: three sessions on one API key, and
    windows refused that a patient second try would have had."""
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    _, report = fetch(Api(history(), {0: [REFUSED] * TRIES}), sleep=sleep)
    backoffs = [w for w in waits if w != angel_history.PAUSE_SECONDS]
    assert backoffs == [3.0, 6.0, 12.0, 24.0] and report.windows[0].attempts == TRIES == 5


def test_a_failure_is_recorded_with_what_it_said_and_no_address():
    boom = ConnectionError("HTTPSConnectionPool(host='x', port=443): Max retries exceeded "
                           "from 203.0.113.77 via 0a:1b:2c:3d:4e:5f (connect timeout=7)")
    _, report = fetch(Api(history(), {0: [boom] * TRIES}))
    error = report.windows[0].error
    assert error.startswith("request raised ConnectionError: HTTPSConnectionPool")
    assert "203.0.113.77" not in error and "0a:1b" not in error and "<address>" in error
    assert len(error) < 200


def test_a_history_is_asked_for_in_whole_exchange_days():
    """A request that began at 14:20 would get that day's later hourly bars
    and not its daily one, which the venue stamps at midnight."""
    api = Api(history())
    _, report = fetch(api, start=datetime(2020, 1, 1, 14, 20, tzinfo=IST))
    assert report.requested_from == "2020-01-01T00:00+05:30"
    assert api.calls[-1]["fromdate"] == "2020-01-01 00:00"
    utc_evening = datetime(2019, 12, 31, 20, 0, tzinfo=timezone.utc)     # 01:30 IST on the 1st
    assert angel_history.day_start(utc_evening).isoformat() == "2020-01-01T00:00:00+05:30"


def test_an_expired_session_is_renewed_once_and_the_window_asked_again():
    logins = []

    async def relogin():
        logins.append(1)

    got, report = fetch(Api(history(), {0: [EXPIRED]}), relogin=relogin)
    assert logins == [1] and report.relogins == 1 and report.complete and len(got) == 1000


def test_when_the_api_is_down_it_stops_asking():
    api = Api(history(3000), {i: [REFUSED] * TRIES for i in range(9)})
    got, report = fetch(api, start=datetime(2010, 1, 1, tzinfo=IST))
    assert got == [] and not report.complete
    assert len(api.calls) == TRIES * angel_history.MAX_CONSECUTIVE_FAILURES
    assert "in a row failed" in report.stopped_early
    assert [w.status for w in report.windows[3:]] == [SKIPPED] * (len(report.windows) - 3)
    assert "no window returned any bars" in report.incomplete_reason


def test_nothing_is_ever_put_in_place_of_bars_that_did_not_arrive():
    """No generated candle, no carried-forward bar, no fallback source."""

    async def dead(_params):
        raise TimeoutError("no answer")

    got, report = fetch(dead)
    assert got == [] and report.rows == 0 and not report.complete
    built = datasets.build("angelone", "RELIANCE", "1d", got, session=NSE_EQUITY,
                           provenance=report.to_dict())
    assert built.bars == 0 and not built.quality_passed


def test_empty_windows_before_the_listing_end_the_search_and_are_not_a_hole():
    listed, until = ist(2023, 6, 1), datetime(2029, 6, 1, tzinfo=IST)
    rows = [r for r in history(2600) if listed <= r[0] <= until.timestamp()]
    api = Api(rows)
    got, report = fetch(api, start=datetime(2005, 1, 1, tzinfo=IST), end=until, now=1.9e9)
    assert len(got) == len(rows) and report.complete
    assert "before the listing" in report.stopped_early
    statuses = [w.status for w in report.windows]
    asked = statuses.index(SKIPPED)
    assert set(statuses[:asked - 2]) == {OK} and statuses[asked - 2:asked] == [EMPTY, EMPTY]
    assert set(statuses[asked:]) == {SKIPPED} and len(api.calls) == asked < len(statuses)


def test_an_empty_window_between_two_with_bars_is_a_hole():
    _, report = fetch(Api(history(), {1: [NOTHING]}))
    assert not report.complete
    assert "empty window sits between two that had bars" in report.incomplete_reason


def test_an_empty_most_recent_window_is_not_taken_for_the_end_of_the_series():
    _, report = fetch(Api(history(), {0: [NOTHING]}))
    assert not report.complete and "most recent window came back empty" in report.incomplete_reason


def test_only_what_the_api_serves_is_asked_for():
    with pytest.raises(HistoryUnavailable, match="serves no 4h candles"):
        fetch(Api(history()), timeframe="4h")
    with pytest.raises(HistoryUnavailable, match="start before its end"):
        fetch(Api(history()), start=END, end=START)
    with pytest.raises(HistoryUnavailable):
        fetch(Api(history()), start=datetime(2020, 1, 1), end=datetime(2024, 1, 1))


# ── reading the venue's rows ──────────────────────────────────

def test_a_stamp_is_exchange_time_whatever_clock_this_machine_keeps():
    was = os.environ.get("TZ")
    try:
        for zone in ("America/New_York", "Asia/Kolkata", "UTC"):
            os.environ["TZ"] = zone
            time.tzset()
            assert parse_time("2024-07-01T09:15:00+05:30") == ist(2024, 7, 1, 9, 15)
            assert parse_time("2024-07-01T09:15:00") == ist(2024, 7, 1, 9, 15)
            assert parse_time("2024-07-01 09:15") == ist(2024, 7, 1, 9, 15)
    finally:
        if was is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = was
        time.tzset()


def test_rows_that_cannot_be_read_are_counted_not_hidden():
    good = ["2024-07-01T09:15:00+05:30", 100, 101, 99, 100.5, 1200]
    rows, malformed = parse_candles([good, ["not a time", 1, 2, 3, 4, 5], good[:3], None])
    assert len(rows) == 1 and malformed == 3

    spoiled = venue(history(40)) + [["garbage"]] * 5
    _, report = fetch(Api([], {0: [{"status": True, "data": spoiled}]}),
                      start=datetime(2023, 6, 1, tzinfo=IST))
    assert report.malformed_rows == 5 and not report.complete
    assert "could not be read" in report.incomplete_reason


# ── the universe ──────────────────────────────────────────────

def test_the_nifty_50_universe_is_fifty_named_companies_and_admits_what_it_is():
    universe = india.load_universe("nifty50")
    assert len(universe.symbols) == len(set(universe.symbols)) == 50
    assert universe.session is NSE_EQUITY and universe.exchange == "NSE"
    assert universe.trading_symbol("M&M") == "M&M-EQ"
    assert universe.source["url"].startswith("https://nsearchives.nseindia.com/")
    assert len(universe.source["sha256"]) == 64 and universe.as_of == universe.version
    assert universe.calendar_reference["trading_symbol"] == "Nifty 50"
    assert universe.membership == "current" and not universe.survivorship_free
    assert any("survivorship" in line for line in universe.limitations)
    assert universe.describe()["survivorship_free"] is False
    assert universe.fingerprint == india.load_universe("nifty50").fingerprint


def test_a_universe_that_cannot_be_trusted_is_refused(tmp_path):
    good = json.loads((india.UNIVERSE_DIR / "nifty50.json").read_text())
    (tmp_path / "twice.json").write_text(json.dumps(
        {**good, "constituents": good["constituents"][:2] + good["constituents"][:1]}))
    (tmp_path / "nowhere.json").write_text(json.dumps({**good, "session": "MOON"}))
    (tmp_path / "broken.json").write_text("{")
    for name, message in (("twice", "twice"), ("nowhere", "nobody has defined"),
                          ("broken", "cannot be read"), ("absent", "No universe")):
        with pytest.raises(india.IndiaDataError, match=message):
            india.load_universe(name, tmp_path)
    edited = {**good, "constituents": good["constituents"][:49]}
    (tmp_path / "nifty50.json").write_text(json.dumps(edited))
    assert india.load_universe("nifty50", tmp_path).fingerprint != india.load_universe().fingerprint


# ── taking a snapshot ─────────────────────────────────────────

DAYS = weekdays(date(2024, 1, 1), 420, holidays=[date(2024, 11, 1)])
FROM = datetime(2023, 12, 1, tzinfo=timezone.utc)
TO = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = TO.timestamp()


def members(*symbols):
    """A small universe shaped like the real one."""
    real = india.load_universe("nifty50")
    keep = tuple(c for c in real.constituents if c["symbol"] in symbols)
    assert len(keep) == len(symbols)
    return india.Universe(
        id="test", version="1", as_of=real.as_of, exchange="NSE", series="EQ",
        session=NSE_EQUITY, membership="current", limitations=real.limitations,
        constituents=keep, source=real.source, calendar_reference=real.calendar_reference,
        fingerprint="test-" + "-".join(sorted(symbols)))


def snapshot(broker, universe, **kwargs):
    return asyncio.run(india.snapshot(broker, universe, "1d", FROM, TO, now=NOW, **kwargs))


def test_a_snapshot_checks_every_member_against_the_index_and_keeps_what_it_finds():
    universe = members("RELIANCE", "SBIN", "INFY", "TCS")
    sbin = daily_rows(DAYS, seed=2)
    sbin = without(sbin, [i for day in DAYS[200:207] for i in on(sbin, day)])
    broker = FakeBroker({
        "Nifty 50": daily_rows(DAYS, seed=9, price=22_000.0),
        "RELIANCE-EQ": daily_rows(DAYS, seed=1),
        "SBIN-EQ": sbin,
        "INFY-EQ": daily_rows(DAYS, seed=3),
    }, incomplete=["INFY-EQ"])
    report = snapshot(broker, universe)
    by = {e.symbol: e for e in report.entries}

    assert "daily bars of NSE Nifty 50" in report.calendar["source"]
    assert report.calendar["days"] == 420 and report.calendar_warnings == []

    assert by["RELIANCE"].status == "accepted" and by["RELIANCE"].reasons == []
    assert by["RELIANCE"].summary["bars"] == 420 and by["RELIANCE"].summary["missing_days"] == 0
    assert by["RELIANCE"].summary["first"] == "2024-01-01"

    assert by["SBIN"].status == "rejected" and by["SBIN"].summary["missing_days"] == 7
    assert any("sessions should have are missing" in r for r in by["SBIN"].reasons)

    assert by["INFY"].status == "rejected"
    assert by["INFY"].reasons[0].startswith("Its history is incomplete: 1 of 3 requests failed")

    assert by["TCS"].status == "unavailable" and by["TCS"].dataset_id is None
    assert "not in the broker's scrip master" in by["TCS"].reasons[0]

    assert len(report.accepted) == 1 and report.to_dict()["accepted"] == 1
    assert any("survivorship" in line for line in report.limitations)

    # Rejected snapshots are stored too: they are evidence about the data.
    stored = india.latest_snapshots(universe, "1d")
    assert set(stored) == {"RELIANCE", "SBIN", "INFY"}
    assert india.accept(stored["RELIANCE"]) == [] and india.accept(stored["SBIN"])
    assert stored["RELIANCE"].provenance["universe"]["fingerprint"] == universe.fingerprint
    assert stored["RELIANCE"].provenance["token"] == broker.token("RELIANCE-EQ")


def test_without_the_index_the_universe_agrees_its_own_calendar_and_says_so():
    universe = members("RELIANCE", "SBIN", "INFY")
    broker = FakeBroker({"RELIANCE-EQ": daily_rows(DAYS, seed=1),
                         "SBIN-EQ": daily_rows(DAYS, seed=2),
                         "INFY-EQ": daily_rows(DAYS, seed=3)})
    report = snapshot(broker, universe, save=False)
    assert report.calendar["source"] == "agreement of 3 members of test 1"
    assert len(report.accepted) == 3


def test_too_few_stocks_to_agree_a_calendar_means_no_snapshot_passes():
    universe = members("RELIANCE", "SBIN")
    broker = FakeBroker({"RELIANCE-EQ": daily_rows(DAYS, seed=1),
                         "SBIN-EQ": daily_rows(DAYS, seed=2)})
    report = snapshot(broker, universe, save=False)
    assert report.calendar is None and report.accepted == []
    assert report.calendar_warnings == ["No calendar could be measured."]
    assert all("not checked against a calendar" in " ".join(e.reasons) for e in report.entries)


def test_a_snapshot_is_only_taken_of_what_the_universe_names():
    with pytest.raises(india.IndiaDataError, match="Not in universe test 1: WIPRO"):
        snapshot(FakeBroker({}), members("RELIANCE", "SBIN", "INFY"), symbols=["WIPRO"])


def test_a_calendar_with_an_impossible_year_is_reported():
    thin = weekdays(date(2023, 6, 1), 520)
    thin = [d for d in thin if not (d.year == 2024 and d.month in (3, 4))]
    calendar = calendar_from_times(NSE_EQUITY, [ist(d.year, d.month, d.day) for d in thin], "t")
    warnings = india.calendar_sanity(calendar)
    assert len(warnings) == 1 and warnings[0].startswith("2024 has ")


# ── the gate ──────────────────────────────────────────────────

def test_a_history_nobody_checked_is_not_one_a_study_may_load():
    """Built the old way — no session, no calendar, no record of its requests
    — a snapshot of an Indian stock passes its quality check having had
    nothing about its gaps measured. The gate refuses it by name."""
    hourly = [[1_700_000_000 // 3600 * 3600 + i * 3600, 100.0, 101.0, 99.0, 100.5, 10.0]
              for i in range(500)]
    legacy = datasets.build("angelone", "RELIANCE", "1h", hourly)
    assert legacy.quality_passed
    reasons = india.accept(legacy)
    assert any("not held to an exchange session" in r for r in reasons)
    assert any("no record of the requests" in r for r in reasons)

    generated = datasets.build("synthetic", "RELIANCE", "1h", hourly)
    assert any("not from a venue" in r for r in india.accept(generated))


def test_a_snapshot_taken_under_another_token_is_flagged():
    universe = members("RELIANCE", "SBIN", "INFY")
    series = {"Nifty 50": daily_rows(DAYS, seed=9), "RELIANCE-EQ": daily_rows(DAYS, seed=1),
              "SBIN-EQ": daily_rows(DAYS, seed=2), "INFY-EQ": daily_rows(DAYS, seed=3)}
    snapshot(FakeBroker(series, tokens={"RELIANCE-EQ": "2885"}), universe)
    stored = india.latest_snapshots(universe, "1d")

    same = FakeBroker(series, tokens={"RELIANCE-EQ": "2885"})
    assert asyncio.run(india.token_changes(same, universe, stored)) == {}

    series_now = {k: v for k, v in series.items() if k != "INFY-EQ"}
    moved = FakeBroker(series_now, tokens={"RELIANCE-EQ": "99999",
                                           "SBIN-EQ": same.token("SBIN-EQ")})
    changes = asyncio.run(india.token_changes(moved, universe, stored))
    assert changes["RELIANCE"] == "snapshot token 2885, broker now lists 99999"
    assert "no longer in the scrip master" in changes["INFY"] and "SBIN" not in changes


# ── research cannot reach an order ────────────────────────────

ORDER_CALLS = {"place_order", "modify_order", "cancel_order", "placeOrder", "modifyOrder",
               "cancelOrder", "placeOrderFullResponse"}


def _imports(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield "." * node.level + (node.module or "")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name


def _names(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, ast.Name):
            yield node.id


def test_the_research_package_imports_nothing_from_the_broker():
    for path in sorted((APP / "research").rglob("*.py")):
        for module in _imports(path):
            assert "broker" not in module.split("."), "{} imports {}".format(path.name, module)


def test_nothing_that_fetches_or_studies_history_names_an_order_call():
    paths = sorted((APP / "research").rglob("*.py")) + [APP / "data" / "angel_history.py"]
    for path in paths:
        used = ORDER_CALLS & set(_names(path))
        assert not used, "{} refers to {}".format(path.name, sorted(used))


def test_the_history_source_reaches_the_session_and_the_scrip_master_and_nothing_else():
    broker_imports = [m for m in _imports(APP / "data" / "angel_history.py") if "broker" in m]
    assert sorted(broker_imports) == ["..broker.angelone.auth", "..broker.angelone.instruments"]


# ── a single stock, the way the live gate's evidence asks for it ──

class AngelNamed:
    """Stands where the Angel One provider does when a session is connected."""
    name = "angelone"

    def supports(self, symbol):
        return True

    async def deep_history(self, symbol, timeframe, bars):
        raise AssertionError("an equity snapshot must not come through the chart's history")

    async def close(self):
        return None


def test_only_nse_cash_equities_have_a_session_defined():
    assert datasets.session_of("RELIANCE", "angelone") is NSE_EQUITY
    for other in ("NIFTY", "USDINR", "MCXGOLD", "NOSUCHSYMBOL"):
        assert datasets.session_of(other, "angelone") is None
    assert datasets.session_of("RELIANCE", "synthetic") is None


def test_an_hourly_snapshot_of_a_stock_keeps_its_bars(monkeypatch):
    """The hourly bars of an NSE stock open at 09:15, 10:15 … On the clock's
    grid every one was dropped: the snapshot had no bars, and the walk-forward
    the live gate asks for could only say "not enough history"."""
    from app.data import angel_history, providers
    from india_fixtures import intraday_rows

    hourly = intraday_rows(DAYS, 60, seed=4)
    broker = FakeBroker({"Nifty 50": daily_rows(DAYS, seed=9, price=22_000.0),
                         "RELIANCE-EQ": hourly})
    monkeypatch.setattr(providers, "build_providers", lambda: {"angelone": AngelNamed()})
    monkeypatch.setattr(angel_history, "AngelHistorySource", lambda: broker)

    taken = asyncio.run(datasets.fetch("RELIANCE", "1h", bars=2_000, now=NOW))
    assert taken.bars > 1_000 and taken.bars % 7 == 0            # whole sessions of seven
    assert taken.rows[0, 0] % 3600 == 2700                       # 09:15 IST is :45 past in UTC
    assert taken.session is NSE_EQUITY and taken.quality_passed and india.accept(taken) == []
    assert taken.provenance["trading_symbol"] == "RELIANCE-EQ" and taken.complete
    assert "Nifty 50" in taken.meta["calendar"]["source"]
    assert ("Nifty 50", "1d") in broker.asked and ("RELIANCE-EQ", "1h") in broker.asked

    # What the clock's grid made of the same rows.
    as_before = datasets.build("angelone", "RELIANCE", "1h", hourly)
    assert as_before.bars == 0


def test_a_stocks_snapshot_fails_loudly_where_it_cannot_be_taken(monkeypatch):
    from app.data import angel_history, providers
    from india_fixtures import intraday_rows

    monkeypatch.setattr(providers, "build_providers", lambda: {"angelone": AngelNamed()})
    index = daily_rows(DAYS, seed=9)

    monkeypatch.setattr(angel_history, "AngelHistorySource",
                        lambda: FakeBroker({"Nifty 50": index}))
    with pytest.raises(datasets.DatasetError, match="RELIANCE-EQ is not in Angel One's scrip"):
        asyncio.run(datasets.fetch("RELIANCE", "1h", bars=500, now=NOW))

    monkeypatch.setattr(angel_history, "AngelHistorySource",
                        lambda: FakeBroker({"Nifty 50": index, "RELIANCE-EQ": []}))
    with pytest.raises(datasets.DatasetError, match="returned no history for RELIANCE 1h"):
        asyncio.run(datasets.fetch("RELIANCE", "1h", bars=500, now=NOW))
    with pytest.raises(datasets.DatasetError, match="serves no 4h bars"):
        asyncio.run(datasets.fetch("RELIANCE", "4h", bars=500, now=NOW))

    # An incomplete history is taken, kept and refused — never quietly shortened.
    broken = FakeBroker({"Nifty 50": index, "RELIANCE-EQ": intraday_rows(DAYS, 60, seed=4)},
                        incomplete=["RELIANCE-EQ"])
    monkeypatch.setattr(angel_history, "AngelHistorySource", lambda: broken)
    taken = asyncio.run(datasets.fetch("RELIANCE", "1h", bars=2_000, now=NOW))
    assert taken.bars > 0 and not taken.quality_passed and not taken.complete
    assert india.accept(taken)[0].startswith("Its history is incomplete")

    # And with no index to count the days by, it does not pass either.
    monkeypatch.setattr(angel_history, "AngelHistorySource", lambda: FakeBroker(
        {"RELIANCE-EQ": intraday_rows(DAYS, 60, seed=4)}))
    blind = asyncio.run(datasets.fetch("RELIANCE", "1h", bars=2_000, now=NOW))
    assert not blind.quality_passed
    assert any("not checked against a calendar" in r for r in india.accept(blind))


def test_a_scrip_master_that_will_not_load_is_a_clean_failure_not_a_missing_symbol(monkeypatch):
    """Seen live: the download timed out with three sessions on one line. That
    is "nothing is known", which must not read as "RELIANCE is not listed"."""
    from app.broker.angelone import instruments
    from app.data import providers

    async def timed_out():
        raise TimeoutError("read timed out from 203.0.113.77")

    monkeypatch.setattr(instruments.angel_instruments, "ensure_loaded", timed_out)
    source = angel_history.AngelHistorySource()
    with pytest.raises(HistoryUnavailable, match="scrip master could not be loaded") as caught:
        asyncio.run(source.scrip("NSE", "RELIANCE-EQ"))
    assert "203.0.113.77" not in str(caught.value)

    monkeypatch.setattr(providers, "build_providers", lambda: {"angelone": AngelNamed()})
    with pytest.raises(datasets.DatasetError, match="scrip master could not be loaded"):
        asyncio.run(datasets.fetch("RELIANCE", "1h", bars=500, now=NOW))
