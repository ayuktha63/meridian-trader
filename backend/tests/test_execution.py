"""Pre-trade risk gate + Angel One live order execution.

These tests exercise the real HTTP path (POST /api/trades/{id}/execute)
against a real DB-backed recommendation, with only the Angel One network
boundary (auth session, instrument resolution, broker calls) replaced by an
in-memory broker. That boundary is exactly what §32 asks to be proven, in
particular: PAPER mode must be structurally unable to reach `placeOrder`, and
an ambiguous broker response must never trigger a blind retry.
"""
from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.broker.angelone import angel_auth, angel_instruments
from app.broker.angelone.auth import AngelSession
from app.broker.angelone.client import AngelOneAmbiguousError, AngelOneOrderError
from app.broker.angelone.instruments import AngelInstrumentMatch, InstrumentUnsupported
from app.broker.trading_mode import PAPER, trading_mode
from app.config import settings
from app.db.models import AnalysisRow, LiveExecutionRow
from app.db.session import session_scope
from app.main import app
from fake_angel import FakeAngel, broker_now


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _reset_drawdown():
    from app.broker.drawdown import drawdown_tracker

    drawdown_tracker._peak = None  # noqa: SLF001 - test-only reset of the singleton
    drawdown_tracker._last = None  # noqa: SLF001
    drawdown_tracker.clear()


@pytest.fixture(autouse=True)
def _reset_broker_state():
    """Every test starts from a clean slate: PAPER, disconnected, no kill switch."""
    trading_mode.set_mode(PAPER)
    trading_mode.deactivate_kill_switch()   # no longer implied by PAPER
    _run(_wipe_executions())                # the live limits count what is open
    angel_auth._session = None  # noqa: SLF001 - test-only reset of the singleton
    _reset_drawdown()
    yield
    trading_mode.set_mode(PAPER)
    trading_mode.deactivate_kill_switch()
    angel_auth._session = None  # noqa: SLF001
    _reset_drawdown()


async def _wipe_executions():
    from sqlalchemy import delete

    async with session_scope() as session:
        await session.execute(delete(LiveExecutionRow))


def _fake_connect():
    """Marks the auth singleton as connected without a real SmartAPI call."""
    angel_auth._session = AngelSession(  # noqa: SLF001
        client=None, client_code="TEST", feed_token="ft", refresh_token="rt",
        profile={}, established_at=time.time())


def _go_live():
    _fake_connect()
    trading_mode.set_mode("LIVE", confirmed=True)


async def _seed_recommendation(decision="BUY", symbol="RELIANCE", entry=2500.0,
                               stop=2480.0, target=2560.0, shares=1.0,
                               risk_amount=None, suggested_lots=1.0,
                               confidence=70.0) -> str:
    """A stored recommendation. `shares` is how many the risk budget should
    buy: the budget is set to exactly that many times the stop distance."""
    if risk_amount is None:
        risk_amount = shares * abs(entry - stop)
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


def _instrument(monkeypatch, match=FAKE_MATCH):
    async def resolve(symbol):
        return match
    monkeypatch.setattr(angel_instruments, "resolve", resolve)


@pytest.fixture
def fake(monkeypatch):
    """LIVE, connected, RELIANCE resolvable, and an in-memory Angel One."""
    _go_live()
    _instrument(monkeypatch)
    return FakeAngel().install(monkeypatch)


def execute(client, rec_id, confirmed=True):
    return client.post(f"/api/trades/{rec_id}/execute", json={"confirmed": confirmed})


# ── risk gate rejections ─────────────────────────────────────

def test_paper_mode_rejects_and_never_calls_place_order(client, fake):
    trading_mode.set_mode(PAPER)
    resp = execute(client, _run(_seed_recommendation()))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "REJECTED"
    assert "live" in body["error_message"].lower() or "mode" in body["error_message"].lower()
    assert fake.placed == [], "placeOrder must never be called in PAPER mode"
    assert fake.order_book_reads == 0, "nor any other broker call"


def test_live_without_confirmation_is_rejected(client, fake):
    assert execute(client, _run(_seed_recommendation()), confirmed=False).json()[
        "status"] == "REJECTED"
    assert fake.placed == []


def test_live_without_angel_connection_is_rejected(client, fake):
    angel_auth._session = None  # noqa: SLF001
    body = execute(client, _run(_seed_recommendation())).json()
    assert body["status"] == "REJECTED"
    assert "not connected" in body["error_message"].lower()


def test_kill_switch_blocks_execution(client, fake):
    trading_mode.activate_kill_switch()
    assert execute(client, _run(_seed_recommendation())).json()["status"] == "REJECTED"
    assert fake.placed == []


def test_a_locked_drawdown_blocks_execution_before_any_broker_call(client, fake):
    """Same shape as the kill switch: fails at the cheap check, before
    instrument resolution or a funds call ever happens."""
    from app.broker.drawdown import drawdown_tracker

    drawdown_tracker.update(10_000.0)
    drawdown_tracker.update(8_000.0)  # past the 10% default — locks
    try:
        assert execute(client, _run(_seed_recommendation())).json()["status"] == "REJECTED"
        assert fake.placed == []
    finally:
        drawdown_tracker.clear()


def test_a_fresh_drawdown_breach_trips_the_kill_switch_too(client, fake, monkeypatch):
    """The drawdown check doesn't only refuse this one order — like the
    daily-loss check, breaching it for the first time trips the kill switch,
    so every *subsequent* order is refused too, not just this one."""
    from app.broker.drawdown import drawdown_tracker

    monkeypatch.setattr(settings, "live_max_drawdown_pct", 10.0, raising=False)
    drawdown_tracker.update(10_000.0)   # the peak, set before the breach
    fake.funds = 8_000.0                # 20% below that peak — past the 10% limit

    assert not trading_mode.kill_switch_active
    try:
        execute(client, _run(_seed_recommendation()))
        assert trading_mode.kill_switch_active
    finally:
        drawdown_tracker.clear()


def test_wait_recommendation_is_not_executable(client, fake):
    assert execute(client, _run(_seed_recommendation(decision="WAIT"))).status_code == 422


def test_unknown_recommendation_404s(client, fake):
    assert execute(client, "does-not-exist").status_code == 404


def test_unsupported_instrument_is_rejected(client, fake, monkeypatch):
    async def resolve(symbol):
        raise InstrumentUnsupported("No matching Angel One contract found for it.")
    monkeypatch.setattr(angel_instruments, "resolve", resolve)

    body = execute(client, _run(_seed_recommendation(symbol="ITC"))).json()
    assert body["status"] == "REJECTED"
    assert "no matching angel one contract" in body["error_message"].lower()


def test_untrusted_cds_tick_conversion_blocks_execution(client, fake, monkeypatch):
    _instrument(monkeypatch, AngelInstrumentMatch(
        symbol="USDINR", exchange="CDS", trading_symbol="USDINR26814FUT",
        token="13873", lot_size=1, tick_size=250.0, instrument_type="FUTCUR"))
    body = execute(client, _run(_seed_recommendation(
        symbol="USDINR", entry=83.20, stop=83.15, target=83.30))).json()
    assert body["status"] == "REJECTED"
    assert "tick" in body["error_message"].lower()


def test_commodity_orders_wait_until_their_quantity_unit_is_verified(client, fake, monkeypatch):
    """Sized here in contract units: one lot of crude is 100. If Angel One
    reads a commodity quantity as lots, that order is a hundred lots."""
    _instrument(monkeypatch, AngelInstrumentMatch(
        symbol="MCXCRUDE", exchange="MCX", trading_symbol="CRUDEOIL26OCTFUT",
        token="430106", lot_size=100, tick_size=1.0, instrument_type="FUTCOM"))
    body = execute(client, _run(_seed_recommendation(
        symbol="MCXCRUDE", entry=6000.0, stop=5980.0, target=6060.0,
        shares=100))).json()
    assert body["status"] == "REJECTED"
    assert "lots" in body["error_message"]
    assert fake.placed == []


def test_an_implausible_tick_size_is_rejected_on_any_exchange(client, fake, monkeypatch):
    _instrument(monkeypatch, AngelInstrumentMatch(
        symbol="RELIANCE", exchange="NSE", trading_symbol="RELIANCE-EQ",
        token="2885", lot_size=1, tick_size=250.0, instrument_type=""))
    body = execute(client, _run(_seed_recommendation())).json()
    assert body["status"] == "REJECTED"
    assert "Tick size" in body["error_message"]
    assert fake.placed == []


def test_invalid_stop_side_is_rejected(client, fake):
    # BUY with stop ABOVE entry — nonsensical, must be rejected before sizing.
    rec_id = _run(_seed_recommendation(decision="BUY", entry=2500.0, stop=2520.0))
    assert execute(client, rec_id).json()["status"] == "REJECTED"
    assert fake.placed == []


def test_a_target_on_the_wrong_side_is_rejected(client, fake):
    body = execute(client, _run(_seed_recommendation(target=2490.0))).json()
    assert body["status"] == "REJECTED"
    assert "profit side" in body["error_message"]


def test_insufficient_funds_is_rejected(client, fake):
    fake.funds = 100.0                       # 1% of that cannot fund one share's risk
    body = execute(client, _run(_seed_recommendation(shares=100))).json()
    assert body["status"] == "REJECTED"
    assert "funds" in body["error_message"].lower()
    assert fake.placed == []


def test_unreadable_funds_refuse_the_order(client, fake, monkeypatch):
    async def down():
        raise AngelOneOrderError("rms unavailable")
    monkeypatch.setattr(angel_auth.__class__, "connected", property(lambda self: True))
    from app.broker.angelone import angel_broker
    monkeypatch.setattr(angel_broker, "get_funds", down)
    body = execute(client, _run(_seed_recommendation())).json()
    assert body["status"] == "REJECTED"
    assert "funds" in body["error_message"].lower()


def test_a_budget_that_cannot_buy_one_lot_is_rejected(client, fake, monkeypatch):
    """Rounding up to one lot would risk a hundred times the budget."""
    _instrument(monkeypatch, AngelInstrumentMatch(
        symbol="RELIANCE", exchange="NSE", trading_symbol="RELIANCE-EQ",
        token="2885", lot_size=500, tick_size=0.1, instrument_type=""))
    body = execute(client, _run(_seed_recommendation(risk_amount=100.0))).json()
    assert body["status"] == "REJECTED"
    assert "cannot buy even one lot" in body["error_message"]
    assert fake.placed == []


# ── sizing ───────────────────────────────────────────────────

def test_quantity_comes_from_the_risk_budget_in_real_shares(client, fake):
    """The regression: sizing used to multiply the recommendation's "lots" by
    the broker lot, so a cash-equity order for 0.37 F&O lots became zero
    shares and one for 3.2 lots became 3 — not the ~800 it was sized for."""
    rec_id = _run(_seed_recommendation(entry=250.0, stop=248.0, target=256.0,
                                       risk_amount=200.0, suggested_lots=0.37))
    body = execute(client, rec_id).json()
    assert body["status"] == "OPEN"
    assert body["requested_quantity"] == 100            # 200 / (250 - 248)
    assert fake.placed[0]["quantity"] == "100"
    assert body["risk_amount"] == 200.0


def test_quantity_rounds_down_to_whole_broker_lots(client, fake, monkeypatch):
    _instrument(monkeypatch, AngelInstrumentMatch(
        symbol="RELIANCE", exchange="NSE", trading_symbol="RELIANCE-EQ",
        token="2885", lot_size=75, tick_size=0.1, instrument_type=""))
    # 440 / (2 x 75) = 2.93 lots -> 2 lots, never 3.
    rec_id = _run(_seed_recommendation(entry=250.0, stop=248.0, target=256.0,
                                       risk_amount=440.0))
    assert execute(client, rec_id).json()["requested_quantity"] == 150


def test_risk_is_capped_by_what_the_account_actually_holds(client, fake):
    """The recommendation was sized against a balance typed into Settings.
    The order answers to the cash Angel One reports."""
    fake.funds = 10_000.0                               # 1% of it is 100
    fake.margin = 2_500.0                               # what 50 shares block intraday
    rec_id = _run(_seed_recommendation(entry=250.0, stop=248.0, target=256.0,
                                       risk_amount=200.0))
    body = execute(client, rec_id).json()
    assert body["status"] == "OPEN", body
    assert body["requested_quantity"] == 50
    assert body["risk_amount"] == 100.0


def test_prices_round_to_the_tick_the_way_that_cannot_add_risk(client, fake, monkeypatch):
    _instrument(monkeypatch, AngelInstrumentMatch(
        symbol="RELIANCE", exchange="NSE", trading_symbol="RELIANCE-EQ",
        token="2885", lot_size=1, tick_size=0.05, instrument_type=""))
    buy = execute(client, _run(_seed_recommendation(
        entry=2500.04, stop=2479.96, target=2560.04))).json()
    # Never pay more than asked; stop and target move toward the entry.
    assert (buy["requested_price"], buy["stop_price"], buy["target_price"]) == (
        2500.0, 2480.0, 2560.0)
    assert fake.placed[0]["price"] == "2500.00"

    sell = execute(client, _run(_seed_recommendation(      # another instrument:
        decision="SELL", symbol="TCS", entry=2499.96,      # one at a time in each
        stop=2520.04, target=2439.96))).json()
    assert (sell["requested_price"], sell["stop_price"], sell["target_price"]) == (
        2500.0, 2520.0, 2440.0)


# ── preview ──────────────────────────────────────────────────

def test_preview_shows_the_exact_order_and_places_nothing(client, fake):
    fake.margin = 5_000.0
    rec_id = _run(_seed_recommendation(entry=250.0, stop=248.0, target=256.0,
                                       risk_amount=200.0))
    body = client.get(f"/api/trades/{rec_id}/preview").json()
    assert body["allowed"] is True
    assert (body["quantity"], body["entry_price"], body["stop_price"]) == (100, 250.0, 248.0)
    assert (body["order_value"], body["risk"], body["margin_required"]) == (
        25_000.0, 200.0, 5_000.0)
    # 25,000 in and 24,800 out, intraday, at Angel One's published rates: Rs 20
    # brokerage a side (0.1%, capped), 1.58 to the exchange and SEBI, 7.48 GST,
    # and 6.99 of STT and stamp duty. (A rate of 0.053% a side said 26.41.)
    assert body["charges"] == pytest.approx(56.06, abs=0.01), "and what the round trip costs"
    assert body["trading_symbol"] == "RELIANCE-EQ"
    assert fake.placed == []
    assert client.get(f"/api/trades/{rec_id}/execution").status_code == 404, "nothing recorded"

    # And it is the same order execute then sends.
    assert execute(client, rec_id).json()["requested_quantity"] == body["quantity"]


def test_preview_explains_a_refusal_without_recording_one(client, fake, monkeypatch):
    monkeypatch.setattr(settings, "live_max_order_value", 10_000.0)
    rec_id = _run(_seed_recommendation(entry=250.0, stop=248.0, risk_amount=200.0))
    body = client.get(f"/api/trades/{rec_id}/preview").json()
    assert body["allowed"] is False
    assert body["check"] == "max_order_value"
    assert "Order value" in body["reason"]
    assert body["quantity"] == 100, "the size it would have been is still shown"
    assert client.get(f"/api/trades/{rec_id}/execution").status_code == 404
    assert client.get("/api/trades/nope/preview").status_code == 404


# ── successful execution ─────────────────────────────────────

def test_successful_execution_calls_place_order_once_and_persists(client, fake):
    rec_id = _run(_seed_recommendation(entry=2500.03))
    resp = execute(client, rec_id)
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
    assert (entry["ordertype"], entry["producttype"]) == ("LIMIT", "INTRADAY")

    fetched = client.get(f"/api/trades/{rec_id}/execution")
    assert fetched.status_code == 200
    assert fetched.json()["broker_order_id"] == "A1001"


# ── broker rejection ──────────────────────────────────────────

def test_broker_rejection_creates_no_position(client, fake):
    fake.fail_next(lambda p: True, AngelOneOrderError(
        "Angel One rejected the order: RMS:Insufficient funds"))
    body = execute(client, _run(_seed_recommendation())).json()
    assert body["status"] == "REJECTED"
    assert body["broker_order_id"] is None
    assert "insufficient funds" in body["error_message"].lower()


# ── ambiguous / timeout ────────────────────────────────────────

def test_ambiguous_response_does_not_retry_and_queries_status(client, fake):
    fake.fail_next(lambda p: True, AngelOneAmbiguousError("submission timed out"))
    reads_before = fake.order_book_reads
    body = execute(client, _run(_seed_recommendation())).json()

    assert len(fake.placed) == 1, "an ambiguous outcome must never be retried"
    assert fake.order_book_reads == reads_before + 1, "must look instead of retrying"
    assert body["status"] == "UNKNOWN"
    assert body["broker_order_id"] is None


def _lookalike():
    return {
        "orderid": "SOMEONE_ELSES", "tradingsymbol": "RELIANCE-EQ", "symboltoken": "2885",
        "transactiontype": "BUY", "quantity": "1", "exchange": "NSE", "ordertag": "",
        "ordertype": "LIMIT", "variety": "NORMAL", "price": "2500.0", "status": "open",
        "filledshares": "0", "averageprice": "0", "updatetime": broker_now()}


def test_ambiguous_response_recovers_only_the_order_carrying_our_tag(client, fake):
    """The timeout case. The order book also holds an older order for the
    same symbol, side and quantity — exactly what the old matcher adopted."""
    fake.orders.append(_lookalike())
    fake.fail_next(lambda p: True, AngelOneAmbiguousError("connection reset"),
                   still_places=True)
    body = execute(client, _run(_seed_recommendation())).json()
    assert body["broker_order_id"] == "A1001"
    assert body["status"] == "SUBMITTED"
    assert len(fake.placed) == 1


def test_ambiguous_response_never_adopts_a_lookalike_order(client, fake):
    fake.orders.append(_lookalike())
    fake.fail_next(lambda p: True, AngelOneAmbiguousError("timed out"))
    rec_id = _run(_seed_recommendation())
    body = execute(client, rec_id).json()
    assert body["status"] == "UNKNOWN"
    assert body["broker_order_id"] is None

    # And while it is unknown, a second click must not send a second order.
    again = execute(client, rec_id).json()
    assert again["status"] == "REJECTED"
    assert len(fake.placed) == 1


# ── duplicate protection ──────────────────────────────────────

def test_duplicate_execution_is_blocked_after_a_successful_submit(client, fake):
    rec_id = _run(_seed_recommendation())
    first, second = execute(client, rec_id), execute(client, rec_id)

    assert first.json()["status"] in ("SUBMITTED", "OPEN", "FILLED")
    assert second.json()["status"] == "REJECTED"
    assert "already has an execution record" in second.json()["error_message"]
    assert len(fake.placed) == 1, "placeOrder must be called exactly once across both attempts"


def test_retry_after_pre_broker_rejection_is_allowed(client, fake):
    """A rejection that never reached the broker (e.g. bad funds) must not
    permanently lock the recommendation — fix the issue, try again."""
    rec_id = _run(_seed_recommendation())

    fake.funds = 1.0                         # too little — rejected pre-broker
    first = execute(client, rec_id).json()
    assert first["status"] == "REJECTED"
    assert first["broker_order_id"] is None

    fake.funds = 1_000_000.0                 # now it's fine
    second = execute(client, rec_id).json()
    assert second["broker_order_id"] == "A1001"
    assert len(fake.placed) == 1


def _run(coro):
    import asyncio
    return asyncio.run(coro)
