"""The live-order supervisor, driven one pass at a time against a fake
Angel One. Each test is one thing that used to be invisible or unprotected."""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

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
from app.db.models import LiveExecutionRow
from app.db.session import session_scope
from app.main import app
from fake_angel import FakeAngel, broker_now
from test_execution import FAKE_MATCH, _fake_connect, _seed_recommendation

QTY = 10


def run(coro):
    return asyncio.run(coro)


def tick():
    reconciler._lock = None  # noqa: SLF001 - a fresh loop per pass
    return run(reconciler.run_once())


async def _row(rec_id) -> LiveExecutionRow:
    async with session_scope() as session:
        from sqlalchemy import select
        return (await session.execute(select(LiveExecutionRow).where(
            LiveExecutionRow.recommendation_id == rec_id))).scalars().first()


def row(rec_id):
    return run(_row(rec_id))


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clean():
    async def wipe():
        async with session_scope() as session:
            from sqlalchemy import delete
            await session.execute(delete(LiveExecutionRow))
    run(wipe())
    alerts.clear()
    trading_mode.set_mode(PAPER)
    trading_mode.deactivate_kill_switch()
    reconciler._flat_seen.clear()            # noqa: SLF001
    reconciler._target_unavailable.clear()   # noqa: SLF001
    reconciler._lock = None                  # noqa: SLF001
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


def submit(client, **kwargs):
    params = dict(entry=2500.0, stop=2480.0, target=2560.0,
                  risk_amount=2000.0, suggested_lots=float(QTY))
    params.update(kwargs)
    rec_id = run(_seed_recommendation(**params))
    body = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True}).json()
    assert body["status"] == "OPEN", body
    return rec_id, body["broker_order_id"]


def filled(client, fake, **kwargs):
    rec_id, entry_id = submit(client, **kwargs)
    fake.fill(entry_id, price=2500.0)
    fake.ltp["2885"] = 2500.0
    tick()
    return rec_id, entry_id


# ── order helpers ─────────────────────────────────────────────

def test_prices_land_on_the_tick_grid_as_clean_strings():
    assert round_to_tick(2500.03, 0.05) == "2500.05"
    assert round_to_tick(2500.0, 0.1) == "2500.0"
    assert round_to_tick(0.1 + 0.2, 0.05) == "0.30"
    assert round_to_tick(83.2012, 0.0025) == "83.2000"
    assert round_to_tick(71503.4, 1.0) == "71503.0"
    with pytest.raises(ValueError):
        round_to_tick(100.0, 0)


def test_broker_timestamps_are_read_as_ist():
    assert parse_broker_time("01-Jan-2026 05:30:00") == pytest.approx(1767225600.0)
    assert parse_broker_time("") is None
    assert parse_broker_time("yesterday") is None


def test_an_order_is_matched_by_tag_and_time_never_by_resemblance():
    now = time.time()
    base = {"tradingsymbol": "RELIANCE-EQ", "transactiontype": "BUY", "quantity": "1",
            "updatetime": broker_now()}
    ours = dict(base, orderid="OURS", ordertag="abc")
    find = lambda orders: find_order_by_tag(  # noqa: E731
        orders, "abc", since=now, trading_symbol="RELIANCE-EQ", side="BUY", quantity=1)
    assert find([dict(base, orderid="X", ordertag=""), ours])["orderid"] == "OURS"
    assert find([dict(base, orderid="X", ordertag="")]) is None
    assert find([dict(ours, updatetime="01-Jan-2020 10:00:00")]) is None   # too old
    assert find([dict(ours, updatetime=None)]) is None                     # undatable
    assert find([dict(ours, quantity="2")]) is None
    assert len(order_tag("f" * 32, "S")) == 20


# ── a stop behind every fill ──────────────────────────────────

def test_a_fill_gets_a_stop_and_a_target_resting_at_the_broker(client, fake):
    rec_id, _ = filled(client, fake)
    stop, = fake.by_type("STOPLOSS_MARKET")
    target = [o for o in fake.orders if o["ordertag"].endswith("T")][0]
    assert (stop["transactiontype"], stop["variety"]) == ("SELL", "STOPLOSS")
    assert stop["triggerprice"] == "2480.0" and stop["quantity"] == str(QTY)
    assert (target["ordertype"], target["price"]) == ("LIMIT", "2560.0")
    r = row(rec_id)
    assert r.status == "FILLED" and r.protection_status == "PROTECTED"
    assert r.stop_order_id == stop["orderid"]
    # Idempotent: more passes place nothing more.
    tick(); tick()
    assert len(fake.placed) == 3


def test_an_unfilled_entry_gets_no_protective_orders(client, fake):
    submit(client)
    tick()
    assert len(fake.placed) == 1


def test_if_no_stop_can_be_placed_the_position_is_flattened(client, fake):
    rec_id, entry_id = submit(client)
    is_stop = lambda p: p["variety"] == "STOPLOSS"  # noqa: E731
    fake.fail_next(is_stop, AngelOneOrderError("SL-M not allowed"))
    fake.fail_next(is_stop, AngelOneOrderError("RMS block"))
    fake.fill(entry_id, price=2500.0)
    fake.ltp["2885"] = 2495.0
    tick()
    exit_order, = fake.by_type("MARKET")
    assert exit_order["transactiontype"] == "SELL" and exit_order["quantity"] == str(QTY)
    assert trading_mode.kill_switch_active
    assert [a["code"] for a in alerts.recent()] == ["STOP_NOT_PLACED"]
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.protection_status) == ("CLOSED", "NO_STOP", "CLOSED")
    assert r.gross_pnl == pytest.approx(-5.0 * QTY)


def test_a_stop_limit_is_tried_where_stop_market_is_refused(client, fake):
    rec_id, entry_id = submit(client)
    fake.fail_next(lambda p: p["ordertype"] == "STOPLOSS_MARKET",
                   AngelOneOrderError("SL-M not allowed"))
    fake.fill(entry_id, price=2500.0)
    tick()
    stop, = fake.by_type("STOPLOSS_LIMIT")
    assert stop["triggerprice"] == "2480.0" and float(stop["price"]) < 2480.0
    assert row(rec_id).protection_status == "PROTECTED"
    assert not trading_mode.kill_switch_active


def test_a_stop_whose_submission_timed_out_is_found_not_duplicated(client, fake):
    rec_id, entry_id = submit(client)
    fake.fail_next(lambda p: p["variety"] == "STOPLOSS",
                   AngelOneAmbiguousError("timeout"), still_places=True)
    fake.fill(entry_id, price=2500.0)
    tick(); tick()
    assert len(fake.by_type("STOPLOSS_MARKET")) == 1
    assert row(rec_id).stop_order_id == fake.by_type("STOPLOSS_MARKET")[0]["orderid"]


# ── one cancels the other ─────────────────────────────────────

def test_stop_fill_closes_the_trade_and_cancels_the_target(client, fake):
    rec_id, _ = filled(client, fake)
    r = row(rec_id)
    fake.fill(r.stop_order_id, price=2479.5)
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.exit_price) == ("CLOSED", "STOP", 2479.5)
    assert r.gross_pnl == pytest.approx(-20.5 * QTY)
    assert fake.order(r.target_order_id)["status"] == "cancelled"
    assert tick() == 0, "a closed trade is no longer supervised"


def test_target_fill_closes_the_trade_and_cancels_the_stop(client, fake):
    rec_id, _ = filled(client, fake)
    r = row(rec_id)
    fake.fill(r.target_order_id, price=2560.0)
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason) == ("CLOSED", "TARGET")
    assert r.gross_pnl == pytest.approx(60.0 * QTY)
    assert fake.order(r.stop_order_id)["status"] == "cancelled"


def test_a_refused_target_is_watched_and_taken_in_software(client, fake):
    """Angel One may want extra margin for a second exit order. The stop
    stays; the target is honoured by price instead."""
    rec_id, entry_id = submit(client)
    fake.fail_next(lambda p: p["ordertag"].endswith("T"), AngelOneOrderError("margin"))
    fake.fill(entry_id, price=2500.0)
    fake.ltp["2885"] = 2510.0
    tick(); tick()
    r = row(rec_id)
    assert r.protection_status == "PROTECTED" and r.target_order_id is None
    assert fake.by_type("MARKET") == []

    fake.ltp["2885"] = 2561.0
    tick()
    assert fake.order(r.stop_order_id)["status"] == "cancelled"
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.exit_price) == ("CLOSED", "TARGET", 2561.0)


# ── entries: recovery, staleness, partial fills ───────────────

def test_an_order_in_flight_across_a_restart_is_recovered_by_tag(client, fake):
    """The process died between sending the order and hearing back."""
    rec_id, entry_id = submit(client)

    async def forget():
        async with session_scope() as session:
            r = (await session.get(LiveExecutionRow, (await _row(rec_id)).id))
            r.broker_order_id, r.status = None, "SUBMITTING"
    run(forget())
    tick()
    r = row(rec_id)
    assert (r.broker_order_id, r.status) == (entry_id, "OPEN")
    assert alerts.recent()[0]["code"] == "ORDER_RECOVERED"


def test_an_order_that_never_appears_is_eventually_written_off(client, fake, monkeypatch):
    fake.fail_next(lambda p: True, AngelOneAmbiguousError("timeout"))
    rec_id = run(_seed_recommendation(suggested_lots=1.0))
    assert client.post(f"/api/trades/{rec_id}/execute",
                       json={"confirmed": True}).json()["status"] == "UNKNOWN"
    tick()
    assert row(rec_id).status == "UNKNOWN"
    monkeypatch.setattr(reconciler_module, "UNKNOWN_GRACE_SECONDS", 0.0)
    tick()
    assert row(rec_id).status == "REJECTED"


def test_an_entry_the_market_never_returns_to_is_cancelled(client, fake, monkeypatch):
    rec_id, entry_id = submit(client)
    tick()
    assert fake.order(entry_id)["status"] == "open"
    monkeypatch.setattr(settings, "live_entry_timeout_seconds", 0.001)
    time.sleep(0.01)
    tick(); tick()
    assert fake.order(entry_id)["status"] == "cancelled"
    assert row(rec_id).status == "CANCELLED"


def test_a_partial_fill_is_capped_and_protected_for_what_filled(client, fake):
    rec_id, entry_id = submit(client)
    fake.fill(entry_id, quantity=4, price=2500.0, done=False)
    tick()
    assert fake.order(entry_id)["status"] == "cancelled"
    stop, = fake.by_type("STOPLOSS_MARKET")
    assert stop["quantity"] == "4"
    tick()
    r = row(rec_id)
    assert (r.status, r.filled_quantity) == ("FILLED", 4.0)


def test_a_broker_rejection_after_acceptance_is_recorded(client, fake):
    rec_id, entry_id = submit(client)
    fake.reject(entry_id, "RMS: margin shortfall")
    tick()
    r = row(rec_id)
    assert r.status == "REJECTED" and "margin" in r.error_message


# ── kill switch and flatten ───────────────────────────────────

def test_kill_switch_cancels_working_entries_but_leaves_stops(client, fake):
    held, _ = filled(client, fake)
    _, working = submit(client)
    resp = client.post("/api/trading/kill-switch?activate=true").json()
    assert resp["kill_switch_active"] and resp["kill_switch_reason"]
    assert fake.order(working)["status"] == "cancelled"
    assert fake.order(row(held).stop_order_id)["status"] == "trigger pending"
    # Still tripped after a mode round-trip.
    client.post("/api/trading/mode", json={"mode": "PAPER"})
    assert client.get("/api/trading/mode").json()["kill_switch_active"]


def test_flatten_all_needs_confirmation_and_closes_everything(client, fake):
    rec_id, _ = filled(client, fake)
    assert client.post("/api/trading/flatten-all", json={}).status_code == 428
    assert fake.by_type("MARKET") == []
    fake.ltp["2885"] = 2507.0
    body = client.post("/api/trading/flatten-all", json={"confirmed": True}).json()
    assert body["closing"] == 1 and body["kill_switch_active"]
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason, r.exit_price) == ("CLOSED", "FLATTEN_ALL", 2507.0)
    assert fake.order(r.stop_order_id or fake.by_type("STOPLOSS_MARKET")[0]["orderid"])[
        "status"] == "cancelled"
    assert len(fake.by_type("MARKET")) == 1


def test_a_position_closed_elsewhere_has_its_orphan_stop_pulled(client, fake, monkeypatch):
    """Closed by hand in Angel One's app. The resting stop would otherwise
    open a short when it triggered."""
    monkeypatch.setattr(reconciler_module, "FLAT_MIN_AGE_SECONDS", 0.0)
    rec_id, _ = filled(client, fake)
    stop_id = row(rec_id).stop_order_id
    fake.positions_override = []
    tick(); tick()
    assert fake.order(stop_id)["status"] == "trigger pending", "one stale read is not proof"
    tick()
    r = row(rec_id)
    assert (r.status, r.exit_reason) == ("CLOSED", "EXTERNAL")
    assert fake.order(stop_id)["status"] == "cancelled"
    assert fake.by_type("MARKET") == []


def test_unreadable_positions_are_never_mistaken_for_flat(client, fake, monkeypatch):
    monkeypatch.setattr(reconciler_module, "FLAT_MIN_AGE_SECONDS", 0.0)
    rec_id, _ = filled(client, fake)

    async def boom():
        raise AngelOneOrderError("positions unavailable")
    monkeypatch.setattr(reconciler_module.angel_broker, "get_positions", boom)
    for _ in range(5):
        tick()
    assert row(rec_id).status == "FILLED"


# ── the ladder moves the real stop ────────────────────────────

def test_the_protection_ladder_modifies_the_resting_stop(client, fake):
    from app.data.store import store

    start = int(time.time()) - 40 * 3600
    store.series("RELIANCE", "1h").replace(
        [[start + i * 3600, 2500.0, 2502.5, 2497.5, 2500.0, 1000.0] for i in range(40)])
    rec_id, _ = filled(client, fake)
    stop_id = row(rec_id).stop_order_id
    assert fake.modified == []

    fake.ltp["2885"] = 2530.0          # +1.5R: the breakeven rung arms
    tick()
    r = row(rec_id)
    assert r.protection_tier == 1
    assert 2500.0 <= r.stop_price < 2530.0
    assert fake.order(stop_id)["triggerprice"] == round_to_tick(r.stop_price, 0.1)
    assert r.initial_stop == 2480.0, "the risk unit never moves"

    fake.ltp["2885"] = 2505.0          # pulling back never loosens it
    tick()
    assert row(rec_id).stop_price == r.stop_price
    assert len(fake.modified) == 1


# ── enforced live limits (the gate's check 11) ────────────────

def attempt(client, **kwargs):
    params = dict(entry=2500.0, stop=2480.0, target=2560.0,
                  risk_amount=2000.0, suggested_lots=float(QTY))
    params.update(kwargs)
    rec_id = run(_seed_recommendation(**params))
    return client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True}).json()


def test_order_value_and_quantity_limits(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_max_order_value", 20_000.0)
    body = attempt(client)                     # 10 x 2500 = 25,000
    assert body["status"] == "REJECTED" and "Order value" in body["error_message"]
    monkeypatch.setattr(settings, "live_max_order_value", 50_000.0)
    monkeypatch.setattr(settings, "live_max_quantity", 5)
    assert "Quantity" in attempt(client)["error_message"]
    assert fake.placed == []


def test_open_position_limit_counts_working_orders_too(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_max_open_positions", 1)
    monkeypatch.setattr(settings, "live_max_symbol_exposure", 0.0)
    assert attempt(client)["status"] == "OPEN"          # resting, unfilled
    body = attempt(client)
    assert body["status"] == "REJECTED" and "already open" in body["error_message"]
    assert len(fake.placed) == 1


def test_symbol_exposure_limit(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_max_symbol_exposure", 40_000.0)
    assert attempt(client)["status"] == "OPEN"          # 25,000
    assert "RELIANCE exposure" in attempt(client)["error_message"]   # would be 50,000


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
    fake.ltp["2885"] = 2560.0                             # entry 2500 is 2.3% away
    assert "stale" in attempt(client)["error_message"]
    fake.ltp["2885"] = 2504.0
    assert attempt(client)["status"] == "OPEN"


def test_market_hours_are_enforced(client, fake, monkeypatch):
    from app.data import sessions

    monkeypatch.setattr(settings, "live_enforce_market_hours", True)
    monkeypatch.setattr(sessions, "is_market_open", lambda ts, asset="fx": False)
    assert "closed" in attempt(client)["error_message"]
    monkeypatch.setattr(sessions, "is_market_open", lambda ts, asset="fx": True)
    assert attempt(client)["status"] == "OPEN"


def test_funds_are_checked_against_broker_margin_not_notional(client, fake, monkeypatch):
    async def poor():
        return {"availablecash": "6000"}
    monkeypatch.setattr(reconciler_module.angel_broker, "get_funds", poor)
    fake.margin = 5_000.0                                 # 5x intraday leverage
    assert attempt(client)["status"] == "OPEN"            # notional 25,000 > 6,000
    fake.margin = 7_000.0
    monkeypatch.setattr(settings, "live_max_open_positions", 0)
    monkeypatch.setattr(settings, "live_max_symbol_exposure", 0.0)
    assert "margin" in attempt(client)["error_message"]


def test_daily_loss_limit_trips_the_kill_switch_as_the_loss_lands(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_daily_loss_limit", 150.0)
    rec_id, _ = filled(client, fake)
    fake.fill(row(rec_id).stop_order_id, price=2480.0)    # -20 x 10 = -200
    tick()
    assert trading_mode.kill_switch_active
    assert "Daily loss limit" in trading_mode.kill_switch_reason
    assert alerts.recent()[-1]["code"] == "DAILY_LOSS_LIMIT"
    body = attempt(client)
    assert body["status"] == "REJECTED" and len(fake.placed) == 3


def test_the_trading_day_starts_at_midnight_ist():
    from app.broker.live_limits import trading_day_start
    # 2026-01-01 00:30 IST is 2025-12-31 19:00 UTC — already the new day.
    assert trading_day_start(1767207600.0) == 1767205800.0
