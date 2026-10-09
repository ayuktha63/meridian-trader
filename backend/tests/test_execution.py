"""Pre-trade risk gate + Angel One live order execution — Phase 4.

These tests exercise the real HTTP path (POST /api/trades/{id}/execute)
against a real DB-backed recommendation, with only the Angel One network
boundary (auth session, instrument resolution, broker calls) mocked. That
boundary is exactly what §32 asks to be proven, in particular: PAPER mode
must be structurally unable to reach `placeOrder`, and an ambiguous broker
response must never trigger a blind retry.
"""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.broker.angelone import angel_auth, angel_broker, angel_instruments
from app.broker.angelone.auth import AngelSession
from app.broker.angelone.client import AngelOneAmbiguousError, AngelOneOrderError
from app.broker.angelone.instruments import AngelInstrumentMatch
from app.broker.trading_mode import PAPER, trading_mode
from app.db.models import AnalysisRow
from app.db.session import session_scope
from app.main import app
from fake_angel import FakeAngel, broker_now


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _reset_broker_state():
    """Every test starts from a clean slate: PAPER, disconnected, no kill switch."""
    trading_mode.set_mode(PAPER)
    trading_mode.deactivate_kill_switch()   # no longer implied by PAPER
    angel_auth._session = None  # noqa: SLF001 - test-only reset of the singleton
    yield
    trading_mode.set_mode(PAPER)
    trading_mode.deactivate_kill_switch()
    angel_auth._session = None  # noqa: SLF001


def _fake_connect():
    """Marks the auth singleton as connected without a real SmartAPI call."""
    angel_auth._session = AngelSession(  # noqa: SLF001
        client=None, client_code="TEST", feed_token="ft", refresh_token="rt",
        profile={}, established_at=time.time())


def _go_live():
    _fake_connect()
    trading_mode.set_mode("LIVE", confirmed=True)


async def _seed_recommendation(decision="BUY", symbol="RELIANCE", entry=2500.0,
                               stop=2480.0, target=2560.0, risk_amount=2000.0,
                               suggested_lots=100.0, confidence=70.0) -> str:
    rec_id = uuid.uuid4().hex
    async with session_scope() as session:
        session.add(AnalysisRow(
            id=rec_id, symbol=symbol, timeframe="1h", decision=decision,
            confidence=confidence, price=entry, source="rule_engine",
            payload={
                "plan": {
                    "entry": entry, "stop_loss": stop, "take_profit": target,
                    "risk_amount": risk_amount, "suggested_lots": suggested_lots,
                },
                "confidence": {"final_confidence": confidence},
            },
            created_at=time.time(),
        ))
    return rec_id


FAKE_MATCH = AngelInstrumentMatch(
    symbol="RELIANCE", exchange="NSE", trading_symbol="RELIANCE-EQ",
    token="2885", lot_size=1, tick_size=0.1, instrument_type="",
)


def _patch_instrument_ok(monkeypatch):
    async def resolve(symbol):
        return FAKE_MATCH
    monkeypatch.setattr(angel_instruments, "resolve", resolve)


def _patch_funds(monkeypatch, available=1_000_000.0):
    async def get_funds():
        return {"availablecash": str(available)}
    monkeypatch.setattr(angel_broker, "get_funds", get_funds)


# ── risk gate rejections ─────────────────────────────────────

def test_paper_mode_rejects_and_never_calls_place_order(client, monkeypatch):
    calls = []
    async def fake_place_order(params):
        calls.append(params)
        return "SHOULD_NOT_HAPPEN"
    monkeypatch.setattr(angel_broker, "place_order", fake_place_order)

    rec_id = _run(_seed_recommendation())
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "REJECTED"
    assert "live" in body["error_message"].lower() or "mode" in body["error_message"].lower()
    assert calls == [], "placeOrder must never be called in PAPER mode"


def test_live_without_confirmation_is_rejected(client, monkeypatch):
    _fake_connect()
    trading_mode.set_mode("LIVE", confirmed=True)
    calls = []
    async def fake_place_order(params):
        calls.append(params)
        return "X"
    monkeypatch.setattr(angel_broker, "place_order", fake_place_order)

    rec_id = _run(_seed_recommendation())
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": False})
    assert resp.json()["status"] == "REJECTED"
    assert calls == []


def test_live_without_angel_connection_is_rejected(client):
    trading_mode.set_mode("LIVE", confirmed=True)  # no _fake_connect()
    rec_id = _run(_seed_recommendation())
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    body = resp.json()
    assert body["status"] == "REJECTED"
    assert "not connected" in body["error_message"].lower()


def test_kill_switch_blocks_execution(client, monkeypatch):
    _go_live()
    trading_mode.activate_kill_switch()
    calls = []
    async def fake_place_order(params):
        calls.append(params)
        return "X"
    monkeypatch.setattr(angel_broker, "place_order", fake_place_order)

    rec_id = _run(_seed_recommendation())
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    assert resp.json()["status"] == "REJECTED"
    assert calls == []


def test_wait_recommendation_is_not_executable(client):
    _go_live()
    rec_id = _run(_seed_recommendation(decision="WAIT"))
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    assert resp.status_code == 422


def test_unknown_recommendation_404s(client):
    _go_live()
    resp = client.post("/api/trades/does-not-exist/execute", json={"confirmed": True})
    assert resp.status_code == 404


def test_unsupported_instrument_is_rejected(client, monkeypatch):
    _go_live()
    async def resolve(symbol):
        from app.broker.angelone.instruments import InstrumentUnsupported
        raise InstrumentUnsupported("Angel One does not support crypto trading.")
    monkeypatch.setattr(angel_instruments, "resolve", resolve)

    rec_id = _run(_seed_recommendation(symbol="BTCUSD"))
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    body = resp.json()
    assert body["status"] == "REJECTED"
    assert "crypto" in body["error_message"].lower()


def test_untrusted_cds_tick_conversion_blocks_execution(client, monkeypatch):
    _go_live()
    cds_match = AngelInstrumentMatch(
        symbol="USDINR", exchange="CDS", trading_symbol="USDINR26814FUT",
        token="13873", lot_size=1, tick_size=250.0, instrument_type="FUTCUR",
    )
    async def resolve(symbol):
        return cds_match
    monkeypatch.setattr(angel_instruments, "resolve", resolve)
    _patch_funds(monkeypatch)

    rec_id = _run(_seed_recommendation(symbol="USDINR", entry=83.20, stop=83.15,
                                       target=83.30))
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    body = resp.json()
    assert body["status"] == "REJECTED"
    assert "tick" in body["error_message"].lower()


def test_invalid_stop_side_is_rejected(client, monkeypatch):
    _go_live()
    _patch_instrument_ok(monkeypatch)
    _patch_funds(monkeypatch)
    # BUY with stop ABOVE entry — nonsensical, must be rejected before sizing.
    rec_id = _run(_seed_recommendation(decision="BUY", entry=2500.0, stop=2520.0))
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    assert resp.json()["status"] == "REJECTED"


def test_insufficient_funds_is_rejected(client, monkeypatch):
    _go_live()
    _patch_instrument_ok(monkeypatch)
    _patch_funds(monkeypatch, available=100.0)  # far less than notional
    rec_id = _run(_seed_recommendation(entry=2500.0, stop=2480.0,
                                       suggested_lots=100.0, risk_amount=2000.0))
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    body = resp.json()
    assert body["status"] == "REJECTED"
    assert "funds" in body["error_message"].lower()


def test_risk_exceeding_budget_is_rejected(client, monkeypatch):
    _go_live()
    # A lot size that forces rounding UP past the recommendation's risk budget
    # would be a genuine bug; here we simulate it by giving a tiny risk budget
    # against a lot size that can't be sized down to fit.
    big_lot_match = AngelInstrumentMatch(
        symbol="RELIANCE", exchange="NSE", trading_symbol="RELIANCE-EQ",
        token="2885", lot_size=500, tick_size=0.1, instrument_type="",
    )
    async def resolve(symbol):
        return big_lot_match
    monkeypatch.setattr(angel_instruments, "resolve", resolve)
    _patch_funds(monkeypatch)

    rec_id = _run(_seed_recommendation(
        entry=2500.0, stop=2480.0, risk_amount=100.0, suggested_lots=1.0))
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    body = resp.json()
    # Either it's rejected for exceeding the risk budget, or (since check 8
    # rounds toward zero) rejected for sizing to zero lots — both are a safe
    # "no" and neither ever reaches placeOrder.
    assert body["status"] == "REJECTED"


# ── successful execution ─────────────────────────────────────

def test_successful_execution_calls_place_order_once_and_persists(client, monkeypatch):
    _go_live()
    _patch_instrument_ok(monkeypatch)
    fake = FakeAngel().install(monkeypatch)

    rec_id = _run(_seed_recommendation(
        entry=2500.03, stop=2480.0, risk_amount=2000.0, suggested_lots=1.0))
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    assert resp.status_code == 200
    body = resp.json()
    entry = fake.placed[0]
    assert len(fake.placed) == 1
    assert body["broker_order_id"] == "A1001"
    assert body["status"] == "OPEN"
    assert body["recommendation_id"] == rec_id
    # On the 0.1 tick grid, as a clean string — not str(float).
    assert entry["price"] == "2500.0"
    assert entry["ordertag"] == rec_id[:20]
    assert entry["ordertype"] == "LIMIT"

    fetched = client.get(f"/api/trades/{rec_id}/execution")
    assert fetched.status_code == 200
    assert fetched.json()["broker_order_id"] == "A1001"


# ── broker rejection ──────────────────────────────────────────

def test_broker_rejection_creates_no_position(client, monkeypatch):
    _go_live()
    _patch_instrument_ok(monkeypatch)
    _patch_funds(monkeypatch)

    async def fake_place_order(params):
        raise AngelOneOrderError("Angel One rejected the order: RMS:Insufficient funds")
    monkeypatch.setattr(angel_broker, "place_order", fake_place_order)

    rec_id = _run(_seed_recommendation(
        entry=2500.0, stop=2480.0, risk_amount=2000.0, suggested_lots=1.0))
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    body = resp.json()
    assert body["status"] == "REJECTED"
    assert body["broker_order_id"] is None
    assert "insufficient funds" in body["error_message"].lower()


# ── ambiguous / timeout ────────────────────────────────────────

def test_ambiguous_response_does_not_retry_and_queries_status(client, monkeypatch):
    _go_live()
    _patch_instrument_ok(monkeypatch)
    _patch_funds(monkeypatch)

    place_calls = []
    async def fake_place_order(params):
        place_calls.append(params)
        raise AngelOneAmbiguousError("Angel One order submission timed out")

    orders_calls = []
    async def fake_get_orders():
        orders_calls.append(1)
        return []  # nothing found — genuinely unknown

    monkeypatch.setattr(angel_broker, "place_order", fake_place_order)
    monkeypatch.setattr(angel_broker, "get_orders", fake_get_orders)

    rec_id = _run(_seed_recommendation(
        entry=2500.0, stop=2480.0, risk_amount=2000.0, suggested_lots=1.0))
    resp = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    body = resp.json()

    assert len(place_calls) == 1, "an ambiguous outcome must never be retried"
    assert len(orders_calls) == 1, "must query order status instead of retrying"
    assert body["status"] == "UNKNOWN"
    assert body["broker_order_id"] is None


def test_ambiguous_response_recovers_only_the_order_carrying_our_tag(client, monkeypatch):
    """The timeout case. The order book also holds an older order for the
    same symbol, side and quantity — exactly what the old matcher adopted."""
    _go_live()
    _patch_instrument_ok(monkeypatch)
    fake = FakeAngel().install(monkeypatch)
    fake.orders.append({
        "orderid": "SOMEONE_ELSES", "tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885",
        "transactiontype": "BUY", "quantity": "1", "exchange": "NSE", "ordertag": "",
        "ordertype": "LIMIT", "variety": "NORMAL", "price": "2500.0", "status": "open",
        "filledshares": "0", "averageprice": "0", "updatetime": broker_now()})
    fake.fail_next(lambda p: True, AngelOneAmbiguousError("connection reset"),
                   still_places=True)

    rec_id = _run(_seed_recommendation(
        entry=2500.0, stop=2480.0, risk_amount=2000.0, suggested_lots=1.0))
    body = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True}).json()
    assert body["broker_order_id"] == "A1001"
    assert body["status"] == "SUBMITTED"
    assert len(fake.placed) == 1


def test_ambiguous_response_never_adopts_a_lookalike_order(client, monkeypatch):
    _go_live()
    _patch_instrument_ok(monkeypatch)
    fake = FakeAngel().install(monkeypatch)
    fake.orders.append({
        "orderid": "LOOKALIKE", "tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885",
        "transactiontype": "BUY", "quantity": "1", "exchange": "NSE", "ordertag": "",
        "ordertype": "LIMIT", "variety": "NORMAL", "price": "2500.0", "status": "open",
        "filledshares": "0", "averageprice": "0", "updatetime": broker_now()})
    fake.fail_next(lambda p: True, AngelOneAmbiguousError("timed out"))

    rec_id = _run(_seed_recommendation(
        entry=2500.0, stop=2480.0, risk_amount=2000.0, suggested_lots=1.0))
    body = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True}).json()
    assert body["status"] == "UNKNOWN"
    assert body["broker_order_id"] is None

    # And while it is unknown, a second click must not send a second order.
    again = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True}).json()
    assert again["status"] == "REJECTED"
    assert len(fake.placed) == 1


# ── duplicate protection ──────────────────────────────────────

def test_duplicate_execution_is_blocked_after_a_successful_submit(client, monkeypatch):
    _go_live()
    _patch_instrument_ok(monkeypatch)
    _patch_funds(monkeypatch)

    calls = []
    async def fake_place_order(params):
        calls.append(params)
        return "ONLYONCE"
    async def fake_status(order_id):
        return {"orderid": order_id, "status": "open", "filledshares": "0"}
    monkeypatch.setattr(angel_broker, "place_order", fake_place_order)
    monkeypatch.setattr(angel_broker, "get_order_status", fake_status)

    rec_id = _run(_seed_recommendation(
        entry=2500.0, stop=2480.0, risk_amount=2000.0, suggested_lots=1.0))

    first = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    second = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})

    assert first.json()["status"] in ("SUBMITTED", "OPEN", "FILLED")
    assert second.json()["status"] == "REJECTED"
    assert "already has an execution record" in second.json()["error_message"]
    assert len(calls) == 1, "placeOrder must be called exactly once across both attempts"


def test_retry_after_pre_broker_rejection_is_allowed(client, monkeypatch):
    """A rejection that never reached the broker (e.g. bad funds) must not
    permanently lock the recommendation — fix the issue, try again."""
    _go_live()
    _patch_instrument_ok(monkeypatch)

    rec_id = _run(_seed_recommendation(
        entry=2500.0, stop=2480.0, risk_amount=2000.0, suggested_lots=1.0))

    _patch_funds(monkeypatch, available=1.0)  # too little — rejected pre-broker
    first = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    assert first.json()["status"] == "REJECTED"
    assert first.json()["broker_order_id"] is None

    _patch_funds(monkeypatch, available=1_000_000.0)  # now it's fine
    calls = []
    async def fake_place_order(params):
        calls.append(params)
        return "SECONDTRY"
    async def fake_status(order_id):
        return {"orderid": order_id, "status": "open", "filledshares": "0"}
    monkeypatch.setattr(angel_broker, "place_order", fake_place_order)
    monkeypatch.setattr(angel_broker, "get_order_status", fake_status)

    second = client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": True})
    assert second.json()["broker_order_id"] == "SECONDTRY"
    assert len(calls) == 1


def _run(coro):
    import asyncio
    return asyncio.run(coro)
