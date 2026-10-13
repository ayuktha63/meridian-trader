"""The live-order supervisor, driven one pass at a time against a fake
Angel One. Each test is one thing that used to be invisible or unprotected."""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import alerts
from app.broker.angelone import angel_auth, angel_instruments
from app.broker.angelone import reconciler as reconciler_module
from app.broker.angelone.client import AngelOneAmbiguousError, AngelOneOrderError
from app.broker.angelone.orders import (
    find_order_by_tag, order_tag, parse_broker_time, round_to_tick,
)
from app.broker.angelone.reconciler import reconciler
from app.broker.trading_mode import PAPER, trading_mode
from app.config import settings
from app.data import sessions
from app.db.models import LiveExecutionRow
from app.db.session import session_scope
from app.main import app
from fake_angel import FakeAngel, broker_now
from test_execution import FAKE_MATCH, _fake_connect, _seed_recommendation

QTY = 10
TOKEN = "2885"


def run(coro):
    return asyncio.run(coro)


def tick(times: int = 1):
    for _ in range(times):
        reconciler._lock = None  # noqa: SLF001 - a fresh event loop per pass
        handled = run(reconciler.run_once())
    return handled


async def _row(rec_id) -> LiveExecutionRow:
    async with session_scope() as session:
        return (await session.execute(select(LiveExecutionRow).where(
            LiveExecutionRow.recommendation_id == rec_id))).scalars().first()


def row(rec_id):
    return run(_row(rec_id))


def update(rec_id, **fields):
    async def apply():
        async with session_scope() as session:
            found = (await session.execute(select(LiveExecutionRow).where(
                LiveExecutionRow.recommendation_id == rec_id))).scalars().first()
            for key, value in fields.items():
                setattr(found, key, value)
    run(apply())


def codes():
    return [a["code"] for a in alerts.recent()]


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clean():
    async def wipe():
        async with session_scope() as session:
            await session.execute(delete(LiveExecutionRow))
    run(wipe())
    alerts.clear()
    trading_mode.set_mode(PAPER)
    trading_mode.deactivate_kill_switch()
    for scratch in (reconciler._flat_seen, reconciler._missing,      # noqa: SLF001
                    reconciler._reduced_seen,                        # noqa: SLF001
                    reconciler._ambiguous, reconciler._triggered,    # noqa: SLF001
                    reconciler._cancel_asked, reconciler._target_unavailable,  # noqa: SLF001
                    reconciler._corrected):                          # noqa: SLF001
        scratch.clear()
    reconciler._lock = None                                          # noqa: SLF001
    yield
    trading_mode.set_mode(PAPER)
    trading_mode.deactivate_kill_switch()
    angel_auth._session = None  # noqa: SLF001


@pytest.fixture
def fake(monkeypatch):
    async def resolve(symbol):
        return FAKE_MATCH
    monkeypatch.setattr(angel_instruments, "resolve", resolve)
    _fake_connect()
    trading_mode.set_mode("LIVE", confirmed=True)
    return FakeAngel().install(monkeypatch)


def attempt(client, **kwargs):
    """Seed a recommendation and try to execute it. Returns the response."""
    params = dict(entry=2500.0, stop=2480.0, target=2560.0, shares=QTY)
    params.update(kwargs)
    rec_id = run(_seed_recommendation(**params))
    body = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True}).json()
    body["rec_id"] = rec_id
    return body


def submit(client, **kwargs):
    body = attempt(client, **kwargs)
    assert body["status"] == "OPEN", body
    return body["rec_id"], body["broker_order_id"]


def filled(client, fake, price=2500.0, **kwargs):
    """A live long of QTY shares, filled, with its stop and target resting."""
    rec_id, entry_id = submit(client, **kwargs)
    fake.fill(entry_id, price=price)
    fake.ltp[TOKEN] = price
    tick()
    return rec_id, entry_id


def stops(fake):
    return [o for o in fake.orders if o["variety"] == "STOPLOSS"]


def targets(fake):
    return [o for o in fake.orders if o["ordertag"].endswith("T")]


def working(orders):
    return [o for o in orders if o["status"] in ("open", "trigger pending")]


# ── order helpers ─────────────────────────────────────────────

def test_prices_land_on_the_tick_grid_as_clean_strings():
    assert round_to_tick(2500.03, 0.05) == "2500.05"
    assert round_to_tick(2500.0, 0.1) == "2500.0"
    assert round_to_tick(0.1 + 0.2, 0.05) == "0.30"
    assert round_to_tick(83.2012, 0.0025) == "83.2000"
    assert round_to_tick(71503.4, 1.0) == "71503.0"
    with pytest.raises(ValueError):
        round_to_tick(100.0, 0)


def test_tick_rounding_can_be_told_which_way_to_go():
    assert round_to_tick(2500.04, 0.05, "down") == "2500.00"
    assert round_to_tick(2500.01, 0.05, "up") == "2500.05"
    assert round_to_tick(2500.05, 0.05, "up") == "2500.05", "already on the grid"
    # Float noise just above a grid point must not cost a whole tick.
    assert round_to_tick(0.1 + 0.2, 0.05, "up") == "0.30"
    assert round_to_tick(2500.1000000000004, 0.05, "up") == "2500.10"


def test_broker_timestamps_are_read_as_ist():
    assert parse_broker_time("01-Jan-2026 05:30:00") == pytest.approx(1767225600.0)
    assert parse_broker_time("") is None
    assert parse_broker_time("yesterday") is None


def test_an_order_is_matched_by_tag_and_time_never_by_resemblance():
    now = time.time()
    base = {"tradingsymbol": "RELIANCE-EQ", "transactiontype": "BUY", "quantity": "1",
            "updatetime": broker_now()}
    ours = dict(base, orderid="OURS", ordertag="abc")

    def find(orders):
        return find_order_by_tag(orders, "abc", since=now, trading_symbol="RELIANCE-EQ",
                                 side="BUY", quantity=1)

    assert find([dict(base, orderid="X", ordertag=""), ours])["orderid"] == "OURS"
    assert find([dict(base, orderid="X", ordertag="")]) is None
    assert find([dict(ours, updatetime="01-Jan-2020 10:00:00")]) is None   # too old
    assert find([dict(ours, updatetime=None)]) is None                     # undatable
    assert find([dict(ours, quantity="2")]) is None
    assert len(order_tag("f" * 32, "S")) == 20


def test_repeating_alerts_are_throttled_per_symbol():
    assert alerts.emit("critical", "FLATTEN_FAILED", "x", throttle=60, symbol="A")
    assert alerts.emit("critical", "FLATTEN_FAILED", "x", throttle=60, symbol="A") is None
    assert alerts.emit("critical", "FLATTEN_FAILED", "x", throttle=60, symbol="B")
    assert alerts.emit("critical", "OTHER", "x", throttle=60, symbol="A")
    assert len(alerts.recent()) == 3


# ── a stop behind every fill ──────────────────────────────────

def test_a_fill_gets_a_stop_and_a_target_resting_at_the_broker(client, fake):
    rec_id, _ = filled(client, fake)
    stop, = stops(fake)
    target, = targets(fake)
    assert (stop["transactiontype"], stop["ordertype"]) == ("SELL", "STOPLOSS_MARKET")
    assert (stop["triggerprice"], stop["quantity"]) == ("2480.0", str(QTY))
    assert (target["ordertype"], target["price"], target["quantity"]) == (
        "LIMIT", "2560.0", str(QTY))
    r = row(rec_id)
    assert (r.status, r.protection_status) == ("FILLED", "PROTECTED")
    assert r.stop_order_id == stop["orderid"]
    assert r.filled_at is not None
    # Idempotent: more passes place nothing more.
    tick(3)
    assert len(fake.placed) == 3


def test_an_unfilled_entry_gets_no_protective_orders(client, fake):
    submit(client)
    tick(2)
    assert len(fake.placed) == 1


def test_if_no_stop_can_be_placed_the_position_is_flattened(client, fake):
    rec_id, entry_id = submit(client)
    for _ in range(2):                       # SL-M, then the SL-limit fallback
        fake.fail_next(lambda p: p["variety"] == "STOPLOSS", AngelOneOrderError("RMS block"))
    fake.fill(entry_id, price=2500.0)
    fake.ltp[TOKEN] = 2495.0
    tick()
    exit_order, = fake.by_type("MARKET")
    assert (exit_order["transactiontype"], exit_order["quantity"]) == ("SELL", str(QTY))
    assert trading_mode.kill_switch_active
    assert codes() == ["STOP_NOT_PLACED"]
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.protection_status) == ("CLOSED", "NO_STOP", "CLOSED")
    assert r.gross_pnl == pytest.approx(-5.0 * QTY)
    assert working(fake.orders) == [], "nothing left resting"


def test_a_stop_limit_is_tried_where_stop_market_is_refused(client, fake):
    rec_id, entry_id = submit(client)
    fake.fail_next(lambda p: p["ordertype"] == "STOPLOSS_MARKET",
                   AngelOneOrderError("SL-M not allowed"))
    fake.fill(entry_id, price=2500.0)
    fake.ltp[TOKEN] = 2500.0
    tick()
    stop, = fake.by_type("STOPLOSS_LIMIT")
    assert stop["triggerprice"] == "2480.0"
    assert float(stop["price"]) < 2480.0
    assert row(rec_id).protection_status == "PROTECTED"
    assert not trading_mode.kill_switch_active


def test_a_stop_whose_submission_timed_out_is_found_not_duplicated(client, fake):
    rec_id, entry_id = submit(client)
    fake.fail_next(lambda p: p["variety"] == "STOPLOSS",
                   AngelOneAmbiguousError("timeout"), still_places=True)
    fake.fill(entry_id, price=2500.0)
    fake.ltp[TOKEN] = 2500.0
    tick(3)
    assert len(stops(fake)) == 1
    r = row(rec_id)
    assert (r.stop_order_id, r.protection_status) == (stops(fake)[0]["orderid"], "PROTECTED")


def test_after_a_timed_out_stop_a_second_is_not_sent_until_the_first_had_time(
        client, fake, monkeypatch):
    """Sent, no answer, and not in the order book yet. Sending another at
    once is how a position ends up with two stops."""
    rec_id, entry_id = submit(client)
    fake.fail_next(lambda p: p["variety"] == "STOPLOSS", AngelOneAmbiguousError("timeout"))
    fake.fill(entry_id, price=2500.0)
    fake.ltp[TOKEN] = 2500.0
    tick(3)
    assert stops(fake) == [], "still inside the grace period"
    assert len([p for p in fake.placed if p["variety"] == "STOPLOSS"]) == 1

    monkeypatch.setattr(reconciler_module, "AMBIGUOUS_GRACE_SECONDS", 0.0)
    tick()
    assert len(stops(fake)) == 1
    assert row(rec_id).protection_status == "PROTECTED"


def test_two_working_stops_are_reduced_to_one(client, fake):
    """The timed-out stop landed after all, and so did its replacement."""
    rec_id, _ = filled(client, fake)
    original = stops(fake)[0]
    fake.orders.append(dict(original, orderid="LATE"))
    tick()
    assert [o["orderid"] for o in working(stops(fake))] == [original["orderid"]]
    assert "DUPLICATE_ORDER" in codes()
    assert row(rec_id).stop_order_id == original["orderid"]


def test_a_stop_missing_from_one_reading_is_not_replaced(client, fake):
    rec_id, _ = filled(client, fake)
    stop = stops(fake)[0]
    fake.orders.remove(stop)                 # one incomplete order-book reading
    tick()
    assert len([p for p in fake.placed if p["variety"] == "STOPLOSS"]) == 1
    fake.orders.append(stop)
    tick()
    assert row(rec_id).stop_order_id == stop["orderid"]
    assert len([p for p in fake.placed if p["variety"] == "STOPLOSS"]) == 1


def test_price_already_through_the_stop_exits_without_tripping_anything(client, fake):
    """Filled, and by the time we look the stop level is gone. A stop order
    would be refused; that is the market, not a malfunction."""
    rec_id, entry_id = submit(client)
    fake.fill(entry_id, price=2500.0)
    fake.ltp[TOKEN] = 2475.0
    tick(2)
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.exit_price) == ("CLOSED", "STOP", 2475.0)
    assert stops(fake) == []
    assert not trading_mode.kill_switch_active


def test_a_stop_limit_the_market_ran_past_is_closed_at_market(client, fake):
    rec_id, entry_id = submit(client)
    fake.fail_next(lambda p: p["ordertype"] == "STOPLOSS_MARKET",
                   AngelOneOrderError("SL-M not allowed"))
    fake.fill(entry_id, price=2500.0)
    fake.ltp[TOKEN] = 2500.0
    tick()
    stop, = stops(fake)
    stop["status"] = "open"                  # triggered: now a resting limit…
    fake.ltp[TOKEN] = 2450.0                 # …that the price has left behind
    tick()
    assert fake.by_type("MARKET") == [], "one reading is not enough to act on"
    tick(2)
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.exit_price) == ("CLOSED", "STOP", 2450.0)
    assert stop["status"] == "cancelled"


# ── one cancels the other ─────────────────────────────────────

def test_stop_fill_closes_the_trade_and_cancels_the_target(client, fake):
    rec_id, _ = filled(client, fake)
    fake.fill(row(rec_id).stop_order_id, price=2479.5)
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.exit_price) == ("CLOSED", "STOP", 2479.5)
    assert r.gross_pnl == pytest.approx(-20.5 * QTY)
    assert targets(fake)[0]["status"] == "cancelled"
    assert tick() == 0, "a closed trade is no longer supervised"


def test_target_fill_closes_the_trade_and_cancels_the_stop(client, fake):
    rec_id, _ = filled(client, fake)
    fake.fill(row(rec_id).target_order_id, price=2560.0)
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason) == ("CLOSED", "TARGET")
    assert r.gross_pnl == pytest.approx(60.0 * QTY)
    assert stops(fake)[0]["status"] == "cancelled"


def test_a_short_is_protected_and_accounted_the_other_way_round(client, fake):
    rec_id, entry_id = submit(client, decision="SELL", entry=2500.0, stop=2520.0,
                              target=2440.0)
    fake.fill(entry_id, price=2500.0)
    fake.ltp[TOKEN] = 2500.0
    tick()
    stop, = stops(fake)
    assert (stop["transactiontype"], stop["triggerprice"]) == ("BUY", "2520.0")
    fake.fill(row(rec_id).target_order_id, price=2440.0)
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason) == ("CLOSED", "TARGET")
    assert r.gross_pnl == pytest.approx(60.0 * QTY)


def test_a_partly_filled_target_shrinks_the_stop_to_what_is_left(client, fake):
    """4 of 10 sold at the target. The trade is not over, and a stop still
    sized for 10 would sell 4 shares that are no longer held."""
    rec_id, _ = filled(client, fake)
    r = row(rec_id)
    fake.fill(r.target_order_id, quantity=4, price=2560.0, done=False)
    tick()
    r = row(rec_id)
    assert r.status == "FILLED", "still open"
    assert fake.order(r.stop_order_id)["quantity"] == "6"

    fake.fill(r.stop_order_id, price=2480.0)
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_quantity) == ("CLOSED", 10.0)
    assert r.exit_price == pytest.approx((4 * 2560.0 + 6 * 2480.0) / 10)
    assert r.gross_pnl == pytest.approx(4 * 60.0 - 6 * 20.0)
    assert working(fake.orders) == []


def test_if_both_exits_fill_the_excess_is_bought_back(client, fake):
    """Stop and target both filled between two readings: 20 sold against 10
    held leaves a short of 10 with nothing behind it."""
    rec_id, _ = filled(client, fake)
    r = row(rec_id)
    fake.fill(r.stop_order_id, price=2480.0)
    fake.fill(r.target_order_id, price=2560.0)
    fake.ltp[TOKEN] = 2500.0
    tick()
    fix, = fake.by_type("MARKET")
    assert (fix["transactiontype"], fix["quantity"]) == ("BUY", str(QTY))
    assert "OVER_EXIT" in codes()
    tick()
    r = row(rec_id)
    assert r.status == "CLOSED"
    # Sold 10 @ 2480 and 10 @ 2560, bought back 10 @ 2500: net 10 out at 2540.
    assert r.gross_pnl == pytest.approx((2480 + 2560 - 2500 - 2500) * QTY)
    assert run(fake.get_positions())[0]["netqty"] == "0"
    tick(2)
    assert len(fake.by_type("MARKET")) == 1, "corrected once, not once per pass"


def test_an_over_exit_the_broker_does_not_confirm_is_only_reported(client, fake):
    rec_id, _ = filled(client, fake)
    r = row(rec_id)
    fake.fill(r.stop_order_id, price=2480.0)
    fake.fill(r.target_order_id, price=2560.0)
    fake.positions_override = [{"exchange": "NSE", "symboltoken": TOKEN, "netqty": "0"}]
    tick(2)
    assert fake.by_type("MARKET") == []
    assert codes().count("OVER_EXIT") == 1, "and not once per pass"
    assert row(rec_id).status == "FILLED", "left for a human"


def test_a_refused_target_is_watched_and_taken_in_software(client, fake):
    """Angel One may want extra margin for a second exit order. The stop
    stays; the target is honoured by price instead."""
    rec_id, entry_id = submit(client)
    fake.fail_next(lambda p: p["ordertag"].endswith("T"), AngelOneOrderError("margin"))
    fake.fill(entry_id, price=2500.0)
    fake.ltp[TOKEN] = 2510.0
    tick(2)
    r = row(rec_id)
    assert r.protection_status == "PROTECTED"
    assert r.target_order_id is None
    assert fake.by_type("MARKET") == []
    assert len([p for p in fake.placed if p["ordertag"].endswith("T")]) == 1, "not retried"

    fake.ltp[TOKEN] = 2561.0
    tick()
    assert stops(fake)[0]["status"] == "cancelled"
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.exit_price) == ("CLOSED", "TARGET", 2561.0)


# ── entries: recovery, staleness, partial fills ───────────────

def test_an_order_in_flight_across_a_restart_is_recovered_by_tag(client, fake):
    """The process died between sending the order and hearing back."""
    rec_id, entry_id = submit(client)
    update(rec_id, broker_order_id=None, status="SUBMITTING")
    tick()
    r = row(rec_id)
    assert (r.broker_order_id, r.status) == (entry_id, "OPEN")
    assert codes() == ["ORDER_RECOVERED"]


def test_an_order_that_never_appears_is_eventually_written_off(client, fake, monkeypatch):
    fake.fail_next(lambda p: True, AngelOneAmbiguousError("timeout"))
    body = attempt(client)
    assert body["status"] == "UNKNOWN"
    tick()
    assert row(body["rec_id"]).status == "UNKNOWN"
    monkeypatch.setattr(reconciler_module, "UNKNOWN_GRACE_SECONDS", 0.0)
    tick()
    assert row(body["rec_id"]).status == "REJECTED"
    # Written off, so the recommendation can be tried again.
    again = client.post(f"/api/trades/{body['rec_id']}/execute",
                        json={"confirmed": True}).json()
    assert again["status"] == "OPEN"


def test_an_entry_the_market_never_returns_to_is_cancelled(client, fake, monkeypatch):
    rec_id, entry_id = submit(client)
    tick()
    assert fake.order(entry_id)["status"] == "open"
    monkeypatch.setattr(settings, "live_entry_timeout_seconds", 0.001)
    time.sleep(0.01)
    tick(2)
    assert fake.order(entry_id)["status"] == "cancelled"
    r = row(rec_id)
    assert r.status == "CANCELLED"
    assert "stale" in r.error_message


def test_staleness_is_measured_from_this_attempt_not_the_first(client, fake):
    """A retry keeps the row, and the row's creation time is the first
    attempt's. Timing the entry from that cancelled every retry on sight."""
    fake.funds = 1.0
    body = attempt(client)
    assert body["status"] == "REJECTED"
    update(body["rec_id"], created_at=time.time() - 3600)
    fake.funds = 1_000_000.0
    again = client.post(f"/api/trades/{body['rec_id']}/execute",
                        json={"confirmed": True}).json()
    assert again["status"] == "OPEN"
    tick(2)
    assert fake.order(again["broker_order_id"])["status"] == "open"


def test_an_order_from_an_earlier_session_is_closed_out(client, fake):
    """Restarted the next morning: yesterday's unfilled DAY order is in no
    order book, and would otherwise count as an open position for ever."""
    rec_id, entry_id = submit(client)
    fake.orders.clear()
    tick()
    assert row(rec_id).status == "OPEN", "missing today is not yet expired"
    update(rec_id, submitted_at=time.time() - 2 * 86_400)
    tick()
    r = row(rec_id)
    assert r.status == "CANCELLED"
    assert "expired" in r.error_message
    assert tick() == 0


def test_a_partial_fill_is_capped_and_protected_for_what_filled(client, fake):
    rec_id, entry_id = submit(client)
    fake.fill(entry_id, quantity=4, price=2500.0, done=False)
    fake.ltp[TOKEN] = 2500.0
    tick()
    assert fake.order(entry_id)["status"] == "cancelled"
    stop, = stops(fake)
    assert stop["quantity"] == "4"
    assert targets(fake)[0]["quantity"] == "4"
    tick()
    r = row(rec_id)
    assert (r.status, r.filled_quantity) == ("FILLED", 4.0)


def test_a_broker_rejection_after_acceptance_is_recorded(client, fake):
    rec_id, entry_id = submit(client)
    fake.reject(entry_id, "RMS: margin shortfall")
    tick()
    r = row(rec_id)
    assert r.status == "REJECTED"
    assert "margin" in r.error_message


# ── kill switch and flatten ───────────────────────────────────

def test_kill_switch_cancels_working_entries_but_leaves_stops(client, fake):
    held, _ = filled(client, fake)
    _, resting = submit(client, symbol="TCS")
    resp = client.post("/api/trading/kill-switch?activate=true").json()
    assert resp["kill_switch_active"]
    assert resp["kill_switch_reason"]
    assert fake.order(resting)["status"] == "cancelled"
    assert fake.order(row(held).stop_order_id)["status"] == "trigger pending"
    # Still tripped after a mode round-trip.
    client.post("/api/trading/mode", json={"mode": "PAPER"})
    assert client.get("/api/trading/mode").json()["kill_switch_active"]


def test_while_the_kill_switch_is_on_no_entry_stays_working(client, fake):
    """An order found late — its submission had timed out — must not sit in
    the market just because it missed the moment the switch was thrown."""
    rec_id, entry_id = submit(client)
    update(rec_id, broker_order_id=None, status="UNKNOWN")
    trading_mode.activate_kill_switch("test")
    tick(2)
    assert fake.order(entry_id)["status"] == "cancelled"
    assert row(rec_id).status == "CANCELLED"


def test_flatten_all_needs_confirmation_and_closes_everything(client, fake):
    rec_id, _ = filled(client, fake)
    assert client.post("/api/trading/flatten-all", json={}).status_code == 428
    assert fake.by_type("MARKET") == []
    fake.ltp[TOKEN] = 2507.0
    body = client.post("/api/trading/flatten-all", json={"confirmed": True}).json()
    assert body["closing"] == 1
    assert body["kill_switch_active"]
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.exit_price) == ("CLOSED", "FLATTEN_ALL", 2507.0)
    assert working(fake.orders) == []
    assert len(fake.by_type("MARKET")) == 1


def test_a_market_exit_is_never_sent_for_shares_the_broker_does_not_show(client, fake):
    filled(client, fake)
    fake.positions_override = [{"exchange": "NSE", "symboltoken": TOKEN, "netqty": "0"}]
    assert client.post("/api/trading/flatten-all",
                       json={"confirmed": True}).json()["closing"] == 0
    assert fake.by_type("MARKET") == []
    assert len(working(stops(fake))) == 1, "and the stop was not cancelled to do it"


def test_flatten_is_capped_by_the_position_the_broker_reports(client, fake):
    rec_id, _ = filled(client, fake)
    fake.positions_override = [{"exchange": "NSE", "symboltoken": TOKEN, "netqty": "6",
                                "ltp": "2500"}]
    client.post("/api/trading/flatten-all", json={"confirmed": True})
    assert fake.by_type("MARKET")[0]["quantity"] == "6"


def test_positions_are_closed_before_the_session_ends(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_squareoff_minutes_before_close", 20.0)
    monkeypatch.setattr(sessions, "minutes_to_close", lambda ts, asset: 60.0)
    rec_id, _ = filled(client, fake)
    tick()
    assert fake.by_type("MARKET") == []
    monkeypatch.setattr(sessions, "minutes_to_close", lambda ts, asset: 12.0)
    fake.ltp[TOKEN] = 2511.0
    tick(2)
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.exit_price) == ("CLOSED", "EOD", 2511.0)
    assert working(fake.orders) == []


# ── closed outside Meridian ───────────────────────────────────

def _sold_by_hand(fake, quantity=QTY, price=2504.0):
    fake.orders.append({
        "orderid": "MANUAL", "tradingsymbol": "RELIANCE-EQ", "symboltoken": TOKEN,
        "transactiontype": "SELL", "quantity": str(quantity), "exchange": "NSE",
        "ordertag": "", "ordertype": "MARKET", "variety": "NORMAL", "price": "0",
        "status": "complete", "filledshares": str(quantity), "averageprice": str(price),
        "updatetime": broker_now()})


def test_a_position_closed_by_hand_has_its_orphan_stop_pulled(client, fake, monkeypatch):
    """Sold in Angel One's own app. The resting stop would otherwise open a
    short when it triggered."""
    monkeypatch.setattr(reconciler_module, "FLAT_MIN_AGE_SECONDS", 0.0)
    rec_id, _ = filled(client, fake)
    stop = stops(fake)[0]
    _sold_by_hand(fake)
    tick(2)
    assert stop["status"] == "trigger pending", "one flat reading is not proof"
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.exit_price) == ("CLOSED", "EXTERNAL", 2504.0)
    assert r.gross_pnl == pytest.approx(4.0 * QTY), "and the manual exit is accounted"
    assert working(fake.orders) == []
    assert fake.by_type("MARKET") == [fake.order("MANUAL")]


def test_a_flat_reading_nothing_explains_does_not_cost_the_stop(client, fake, monkeypatch):
    """The positions endpoint says flat, but no order in the book sold
    anything. Cancelling the stop on that would leave a real position bare."""
    monkeypatch.setattr(reconciler_module, "FLAT_MIN_AGE_SECONDS", 0.0)
    rec_id, _ = filled(client, fake)
    fake.positions_override = []
    tick(6)
    assert row(rec_id).status == "FILLED"
    assert len(working(stops(fake))) == 1
    assert codes().count("POSITION_MISMATCH") == 1


def test_unreadable_positions_are_never_mistaken_for_flat(client, fake, monkeypatch):
    monkeypatch.setattr(reconciler_module, "FLAT_MIN_AGE_SECONDS", 0.0)
    rec_id, _ = filled(client, fake)

    async def boom():
        raise AngelOneOrderError("positions unavailable")
    monkeypatch.setattr(reconciler_module.angel_broker, "get_positions", boom)
    tick(5)
    assert row(rec_id).status == "FILLED"
    assert len(working(stops(fake))) == 1


def test_a_position_from_a_finished_session_is_recorded_as_squared_off(
        client, fake, monkeypatch):
    monkeypatch.setattr(reconciler_module, "FLAT_MIN_AGE_SECONDS", 0.0)
    rec_id, _ = filled(client, fake)
    fake.orders.clear()                      # a new day: a new, empty order book
    fake.positions_override = []
    update(rec_id, filled_at=time.time() - 2 * 86_400,
           submitted_at=time.time() - 2 * 86_400)
    sent = len(fake.placed)
    tick(4)
    r = row(rec_id)
    assert (r.status, r.exit_reason) == ("CLOSED", "SQUARED_OFF")
    assert len(fake.placed) == sent, "nothing is sent for a position long gone"


def test_no_order_is_placed_for_a_position_the_broker_does_not_show(client, fake):
    """We still count it open, its stop is gone, and Angel One reports
    nothing held. A fresh stop there is a sell order with no shares behind it."""
    rec_id, _ = filled(client, fake)
    update(rec_id, filled_at=time.time() - 60)      # not a fill still settling
    for order in working(fake.orders):
        order["status"] = "cancelled"               # pulled by hand, in the app
    fake.positions_override = []
    sent = len(fake.placed)
    tick(6)
    assert len(fake.placed) == sent
    assert row(rec_id).status == "FILLED", "and not written off either: nothing explains it"
    assert "POSITION_MISMATCH" in codes()


def test_a_fill_still_settling_is_protected_before_the_position_shows(client, fake):
    """The other side of the same rule: seconds after a fill the positions
    endpoint can lag the order book, and the stop does not wait for it."""
    rec_id, entry_id = submit(client)
    fake.fill(entry_id, price=2500.0)
    fake.ltp[TOKEN] = 2500.0
    fake.positions_override = []
    tick()
    assert len(working(stops(fake))) == 1
    assert row(rec_id).protection_status == "PROTECTED"
    # …nor is it cut down to a position still catching up with the fills.
    fake.positions_override = [{"exchange": "NSE", "symboltoken": TOKEN,
                                "producttype": "INTRADAY", "netqty": "4", "ltp": "2500"}]
    tick(3)
    assert stops(fake)[0]["quantity"] == str(QTY)


def _left_by_an_older_version(age_seconds):
    """A FILLED row as the release before this one wrote it: no tag, no fill
    time, no child orders — it never tracked an exit, so it stayed FILLED."""
    rec_id = uuid.uuid4().hex
    sent = time.time() - age_seconds

    async def add():
        async with session_scope() as session:
            session.add(LiveExecutionRow(
                recommendation_id=rec_id, symbol="RELIANCE", side="BUY", exchange="NSE",
                trading_symbol="RELIANCE-EQ", instrument_token=TOKEN,
                requested_quantity=QTY, filled_quantity=QTY, requested_price=2500.0,
                average_fill_price=2500.0, stop_price=2480.0, target_price=2560.0,
                status="FILLED", broker_order_id="OLD1", created_at=sent,
                updated_at=sent))
    run(add())
    return rec_id


def test_a_fill_an_older_version_left_open_is_closed_out_not_protected(client, fake):
    """Upgrading must not rest a stop for every trade the old code ever
    filled and never saw close."""
    rec_id = _left_by_an_older_version(2 * 86_400)
    fake.ltp[TOKEN] = 2500.0
    tick(4)
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.gross_pnl) == ("CLOSED", "SQUARED_OFF", None)
    assert fake.placed == []


def test_a_fill_an_older_version_left_open_gets_its_stop_if_still_held(client, fake):
    rec_id = _left_by_an_older_version(3600)
    fake.positions_override = [{"exchange": "NSE", "symboltoken": TOKEN,
                                "producttype": "INTRADAY", "netqty": str(QTY),
                                "ltp": "2500"}]
    tick()
    stop = working(stops(fake))[0]
    assert (stop["quantity"], stop["triggerprice"]) == (str(QTY), "2480.00")
    assert row(rec_id).protection_status == "PROTECTED"


def test_shares_sold_by_hand_shrink_the_stop_and_target_to_what_is_left(client, fake):
    """Four of ten sold in Angel One's app. A stop still resting for ten
    would sell six that are held and four that are not."""
    rec_id, _ = filled(client, fake)
    update(rec_id, filled_at=time.time() - 60)
    _sold_by_hand(fake, quantity=4)
    tick()
    assert stops(fake)[0]["quantity"] == str(QTY), "one reading is not proof"
    tick()
    assert stops(fake)[0]["quantity"] == "6"
    assert targets(fake)[0]["quantity"] == "6"
    assert row(rec_id).protected_quantity == 6
    assert codes().count("POSITION_REDUCED") == 1

    fake.fill(row(rec_id).stop_order_id, price=2480.0)
    sent = len(fake.placed)
    tick(4)
    r = row(rec_id)
    assert r.status == "CLOSED"
    assert r.gross_pnl == pytest.approx(6 * -20.0 + 4 * 4.0), "both exits accounted"
    assert run(fake.get_positions())[0]["netqty"] == "0", "flat — not short four"
    assert len(fake.placed) == sent
    assert working(fake.orders) == []


def test_shares_bought_back_by_hand_are_covered_again(client, fake):
    rec_id, _ = filled(client, fake)
    update(rec_id, filled_at=time.time() - 60)
    fake.positions_override = [{"exchange": "NSE", "symboltoken": TOKEN,
                                "producttype": "INTRADAY", "netqty": "6", "ltp": "2500"}]
    tick(2)
    assert stops(fake)[0]["quantity"] == "6"
    fake.positions_override = None                   # the book shows all ten again
    tick()
    assert stops(fake)[0]["quantity"] == str(QTY)
    assert targets(fake)[0]["quantity"] == str(QTY)


def test_a_delivery_holding_in_the_same_stock_is_not_read_as_the_position(client, fake):
    """Angel One lists a position per product. Three shares held for delivery
    are not seven of the intraday ten gone missing."""
    rec_id, _ = filled(client, fake)
    update(rec_id, filled_at=time.time() - 60)
    fake.positions_override = [
        {"exchange": "NSE", "symboltoken": TOKEN, "producttype": "DELIVERY",
         "netqty": "3", "ltp": "2500"},
        {"exchange": "NSE", "symboltoken": TOKEN, "producttype": "INTRADAY",
         "netqty": str(QTY), "ltp": "2500"}]
    tick(3)
    assert stops(fake)[0]["quantity"] == str(QTY)
    assert "POSITION_REDUCED" not in codes()


def test_an_execution_that_cannot_be_checked_raises_an_alert(client, fake, monkeypatch):
    """One row failing must not stop the others — but it must not fail in
    silence either: that row is a position nobody is watching."""
    filled(client, fake)

    async def broken(view):
        raise RuntimeError("something nobody anticipated")
    monkeypatch.setattr(reconciler, "_keep_stop", broken)
    assert tick() == 1
    assert codes()[-1] == "SUPERVISION_FAILED"


# ── the ladder moves the real stop ────────────────────────────

def _history(symbol="RELIANCE", timeframe="1h"):
    from app.data.store import store

    start = int(time.time()) - 40 * 3600
    store.series(symbol, timeframe).replace(
        [[start + i * 3600, 2500.0, 2502.5, 2497.5, 2500.0, 1000.0] for i in range(40)])


def test_the_protection_ladder_modifies_the_resting_stop(client, fake):
    _history()
    rec_id, _ = filled(client, fake)
    stop_id = row(rec_id).stop_order_id
    assert fake.modified == []

    fake.ltp[TOKEN] = 2530.0           # +1.5R: the breakeven rung arms
    tick()
    r = row(rec_id)
    assert r.protection_tier == 1
    assert 2500.0 <= r.stop_price < 2530.0
    assert fake.order(stop_id)["triggerprice"] == round_to_tick(r.stop_price, 0.1)
    assert fake.order(stop_id)["ordertype"] == "STOPLOSS_MARKET"
    assert r.initial_stop == 2480.0, "the risk unit never moves"

    fake.ltp[TOKEN] = 2505.0           # pulling back never loosens it
    tick()
    assert row(rec_id).stop_price == r.stop_price
    assert len(fake.modified) == 1


def test_the_ladder_keeps_a_stop_limit_a_stop_limit(client, fake):
    _history()
    rec_id, entry_id = submit(client)
    fake.fail_next(lambda p: p["ordertype"] == "STOPLOSS_MARKET",
                   AngelOneOrderError("SL-M not allowed"))
    fake.fill(entry_id, price=2500.0)
    fake.ltp[TOKEN] = 2500.0
    tick()
    fake.ltp[TOKEN] = 2530.0
    tick()
    sent = fake.modified[-1]
    assert sent["ordertype"] == "STOPLOSS_LIMIT"
    assert float(sent["price"]) < float(sent["triggerprice"]), "limit follows the trigger"


def test_the_ladder_is_off_when_auto_protect_is_off(client, fake, monkeypatch):
    from app import runtime_settings

    _history()
    monkeypatch.setattr(runtime_settings.store, "_settings",
                        runtime_settings.get().model_copy(update={"auto_protect": False}))
    filled(client, fake)
    fake.ltp[TOKEN] = 2530.0
    tick(2)
    assert fake.modified == []


# ── enforced live limits (the gate's check 11) ────────────────

def test_order_value_and_quantity_limits(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_max_order_value", 20_000.0)
    body = attempt(client)                     # 10 x 2500 = 25,000
    assert body["status"] == "REJECTED"
    assert "Order value" in body["error_message"]
    monkeypatch.setattr(settings, "live_max_order_value", 50_000.0)
    monkeypatch.setattr(settings, "live_max_quantity", 5)
    assert "Quantity" in attempt(client)["error_message"]
    assert fake.placed == []


def test_open_position_limit_counts_working_orders_too(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_max_open_positions", 1)
    monkeypatch.setattr(settings, "live_max_symbol_exposure", 0.0)
    assert attempt(client)["status"] == "OPEN"          # resting, unfilled
    body = attempt(client)
    assert body["status"] == "REJECTED"
    assert "already open" in body["error_message"]
    assert len(fake.placed) == 1


def test_symbol_exposure_limit(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_max_symbol_exposure", 20_000.0)
    assert "RELIANCE exposure" in attempt(client)["error_message"]   # 25,000
    monkeypatch.setattr(settings, "live_max_symbol_exposure", 40_000.0)
    assert attempt(client)["status"] == "OPEN"


def test_sector_exposure_limit_adds_up_a_sector(client, fake, monkeypatch):
    """Two banks are one sector; a bank and a software exporter are not.
    Their futures trade in different lots, which has nothing to do with it."""
    monkeypatch.setattr(settings, "live_max_symbol_exposure", 0.0)
    monkeypatch.setattr(settings, "live_max_sector_exposure", 40_000.0)
    assert attempt(client, symbol="HDFCBANK")["status"] == "OPEN"        # 25,000
    second = attempt(client, symbol="SBIN")                              # 25,000 more
    assert second["status"] == "REJECTED"
    assert "Financials sector exposure would be 50000.00" in second["error_message"]
    assert attempt(client, symbol="INFY")["status"] == "OPEN", "IT has its own room"
    assert len(fake.placed) == 2


def test_one_live_execution_per_instrument(client, fake):
    """Angel One nets every order in an instrument into one position, so a
    second — adding to it or reversing it — cannot be protected on its own."""
    filled(client, fake)
    again = attempt(client)
    assert again["status"] == "REJECTED"
    assert "already has a live position" in again["error_message"]
    reverse = attempt(client, decision="SELL", stop=2520.0, target=2440.0)
    assert reverse["status"] == "REJECTED"
    assert "already has a live position" in reverse["error_message"]
    assert len(fake.placed) == 3, "the entry, its stop and its target — nothing since"
    assert attempt(client, symbol="TCS")["status"] == "OPEN", "another instrument is fine"


def test_trades_per_day_limit(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_max_trades_per_day", 1)
    monkeypatch.setattr(settings, "live_max_open_positions", 0)
    monkeypatch.setattr(settings, "live_max_symbol_exposure", 0.0)
    assert attempt(client)["status"] == "OPEN"
    assert "already today" in attempt(client)["error_message"]


def test_entry_far_from_the_market_is_refused(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_price_band_pct", 1.0)
    body = attempt(client)
    assert "current price" in body["error_message"], "no quote means no order"
    fake.ltp[TOKEN] = 2560.0                              # entry 2500 is 2.3% away
    assert "stale" in attempt(client)["error_message"]
    fake.ltp[TOKEN] = 2504.0
    assert attempt(client)["status"] == "OPEN"


def test_market_hours_and_the_closing_window_are_enforced(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_enforce_market_hours", True)
    monkeypatch.setattr(sessions, "is_market_open", lambda ts, asset="fx": False)
    assert "closed" in attempt(client)["error_message"]

    monkeypatch.setattr(sessions, "is_market_open", lambda ts, asset="fx": True)
    monkeypatch.setattr(sessions, "minutes_to_close", lambda ts, asset: 30.0)
    late = attempt(client)
    assert late["status"] == "REJECTED"
    assert "minutes to the close" in late["error_message"]

    monkeypatch.setattr(sessions, "minutes_to_close", lambda ts, asset: 200.0)
    assert attempt(client)["status"] == "OPEN"


def test_minutes_to_close_follows_the_segments_own_hours():
    # Wed 2026-01-07 09:00 UTC is 14:30 IST: NSE closes at 15:30, MCX at 23:30.
    ts = 1767776400.0
    assert sessions.minutes_to_close(ts, "stock") == pytest.approx(60.0)
    assert sessions.minutes_to_close(ts, "index") == pytest.approx(60.0)
    assert sessions.minutes_to_close(ts, "commodity") == pytest.approx(540.0)
    assert sessions.minutes_to_close(ts, "fx") is None, "not an exchange session"
    assert sessions.minutes_to_close(ts + 3 * 3600, "stock") is None, "already shut"
    assert sessions.minutes_to_close(ts + 3 * 86_400, "stock") is None, "Saturday"


def test_funds_are_checked_against_broker_margin_not_notional(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_max_risk_pct_of_funds", 0.0)
    monkeypatch.setattr(settings, "live_max_open_positions", 0)
    monkeypatch.setattr(settings, "live_max_symbol_exposure", 0.0)
    fake.funds = 6_000.0
    fake.margin = 5_000.0                                 # 5x intraday leverage
    assert attempt(client)["status"] == "OPEN"            # notional 25,000 > 6,000
    fake.margin = 7_000.0
    assert "margin" in attempt(client)["error_message"]
    fake.margin = None                                    # the broker will not say
    assert "notional" in attempt(client)["error_message"]


def test_the_days_worst_case_is_checked_before_the_order_not_after(client, fake, monkeypatch):
    """Realised loss alone lets the day run past its limit: 150 down with a
    200-risk order still to be stopped out is a 350 day."""
    monkeypatch.setattr(settings, "live_daily_loss_limit", 350.0)
    monkeypatch.setattr(settings, "live_max_symbol_exposure", 0.0)
    assert attempt(client)["status"] == "OPEN"            # risks 200
    body = attempt(client)                                # another 200: 400 > 350
    assert body["status"] == "REJECTED"
    assert "daily loss limit" in body["error_message"]
    assert not trading_mode.kill_switch_active, "nothing has been lost yet"


def test_a_stop_at_breakeven_frees_the_risk_it_was_holding(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_daily_loss_limit", 350.0)
    monkeypatch.setattr(settings, "live_max_symbol_exposure", 0.0)
    rec_id, _ = filled(client, fake)
    update(rec_id, stop_price=2500.0)                     # ratcheted to the entry
    assert attempt(client, symbol="TCS")["status"] == "OPEN"
    assert "daily loss limit" in attempt(client, symbol="INFY")["error_message"]


def test_daily_loss_limit_trips_the_kill_switch_as_the_loss_lands(client, fake, monkeypatch):
    # Room for the order as planned (200 at the stop and 56 in charges), and
    # none for the one it turns into.
    monkeypatch.setattr(settings, "live_daily_loss_limit", 260.0)
    rec_id, _ = filled(client, fake)
    fake.fill(row(rec_id).stop_order_id, price=2470.0)    # slipped: -30 x 10 = -300
    tick()
    assert trading_mode.kill_switch_active
    assert "Daily loss limit" in trading_mode.kill_switch_reason
    assert codes()[-1] == "DAILY_LOSS_LIMIT"
    body = attempt(client)
    assert body["status"] == "REJECTED"
    assert len(fake.placed) == 3


def test_the_daily_limit_counts_what_the_broker_and_exchange_take(client, fake, monkeypatch):
    """Stopped exactly at the stop is 200 lost on price — and 56 more in
    brokerage and statutory charges. The day's limit is on what is gone."""
    rec_id, _ = filled(client, fake)
    monkeypatch.setattr(settings, "live_daily_loss_limit", 215.0)
    fake.fill(row(rec_id).stop_order_id, price=2480.0)
    tick()
    assert row(rec_id).gross_pnl == pytest.approx(-200.0)
    assert trading_mode.kill_switch_active
    assert "256.06 lost today" in trading_mode.kill_switch_reason


def test_the_days_worst_case_includes_the_charges(client, fake, monkeypatch):
    """An order that fits the limit on price alone, and not once its round
    trip is paid for, does not fit."""
    monkeypatch.setattr(settings, "live_daily_loss_limit", 215.0)
    body = attempt(client)                                # 200 at the stop + 56
    assert body["status"] == "REJECTED"
    assert "daily loss limit" in body["error_message"]
    monkeypatch.setattr(settings, "live_daily_loss_limit", 260.0)
    assert attempt(client)["status"] == "OPEN"


def test_a_breakeven_stop_still_owes_the_round_trip(client, fake, monkeypatch):
    """Out at the entry price is not out for free."""
    rec_id, _ = filled(client, fake)
    update(rec_id, stop_price=2500.0)
    monkeypatch.setattr(settings, "live_daily_loss_limit", 290.0)
    # The next order would lose 256 at its stop: inside 290 alone, outside it
    # with the 56 the open trade still owes.
    assert "daily loss limit" in attempt(client, symbol="TCS")["error_message"]


def test_a_closed_trade_reports_its_charges_and_what_is_left(client, fake):
    rec_id, _ = filled(client, fake)
    fake.fill(row(rec_id).target_order_id, price=2560.0)
    tick()
    record = client.get("/api/trades/live").json()[0]
    assert record["gross_pnl"] == pytest.approx(600.0)
    # Equity intraday on 25,000 in and 25,600 out, as Angel One charges it:
    # 20 + 20 brokerage, 1.60 exchange and SEBI, 7.49 GST, 6.40 STT on the
    # sale and 0.75 stamp duty on the purchase.
    assert record["charges"] == pytest.approx(56.24, abs=0.01)
    assert record["net_pnl"] == pytest.approx(600.0 - 56.24, abs=0.01)


def test_the_trading_day_starts_at_midnight_ist():
    from app.broker.live_limits import trading_day_start
    # 2026-01-01 00:30 IST is 2025-12-31 19:00 UTC — already the new day.
    assert trading_day_start(1767207600.0) == 1767205800.0


# ── audit trail and alert channel ─────────────────────────────

def test_everything_that_touches_real_money_is_audited(client, fake):
    _history()
    rec_id, _ = filled(client, fake)
    fake.ltp[TOKEN] = 2530.0
    tick()                                            # the ladder moves the stop
    fake.fill(row(rec_id).stop_order_id, price=2500.0)
    tick()
    client.post("/api/trading/kill-switch?activate=true")
    attempt(client)                                   # refused: kill switch
    log = client.get("/api/audit").json()
    events = [e["event"] for e in log]
    for expected in ("order_submitting", "order_submitted", "stop_placed", "stop_moved",
                     "position_closed", "kill_switch", "gate_rejected"):
        assert expected in events, expected
    closed = next(e for e in log if e["event"] == "position_closed")
    assert closed["subject"] == rec_id
    assert closed["detail"]["reason"] == "STOP"


def test_telegram_gets_warnings_and_criticals_but_never_the_token_in_a_log(monkeypatch, caplog):
    from app import notify

    sent = []
    monkeypatch.setattr(settings, "telegram_bot_token", "123:SECRET")
    monkeypatch.setattr(settings, "telegram_chat_id", "42")
    monkeypatch.setattr(notify.httpx, "post", lambda url, **kw: sent.append((url, kw)))
    notify._send({"level": "critical", "code": "STOP_NOT_PLACED", "message": "boom",
                  "context": {"symbol": "RELIANCE"}})
    assert "123:SECRET" in sent[0][0]
    assert sent[0][1]["json"]["chat_id"] == "42"
    assert "RELIANCE" in sent[0][1]["json"]["text"]

    def fail(url, **kw):
        raise RuntimeError("could not reach " + url)
    monkeypatch.setattr(notify.httpx, "post", fail)
    notify._send({"level": "warning", "code": "X", "message": "y", "context": {}})
    assert "SECRET" not in caplog.text
