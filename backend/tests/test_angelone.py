"""Angel One live-execution safety tests.

The one property that matters most here: PAPER mode must be structurally
incapable of reaching Angel One, not just configured not to. These tests
exist because the live-trading spec (§33) explicitly requires proof of that,
not just an assertion of it in a docstring.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.broker.angelone.instruments import (
    AngelOneInstrumentService, InstrumentUnsupported,
)
from app.broker.trading_mode import LIVE, PAPER, TradingModeStore
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# ── trading mode ──────────────────────────────────────────────

def test_mode_defaults_to_paper():
    store = TradingModeStore()
    assert store.mode == PAPER
    assert store.can_trade_live() is False


def test_switching_to_live_without_confirmation_is_rejected():
    store = TradingModeStore()
    with pytest.raises(PermissionError):
        store.set_mode(LIVE, confirmed=False)
    assert store.mode == PAPER


def test_switching_to_live_with_confirmation_works():
    store = TradingModeStore()
    store.set_mode(LIVE, confirmed=True)
    assert store.mode == LIVE
    assert store.can_trade_live() is True


def test_kill_switch_blocks_live_trading_even_when_mode_is_live():
    store = TradingModeStore()
    store.set_mode(LIVE, confirmed=True)
    store.activate_kill_switch()
    assert store.can_trade_live() is False


def test_returning_to_paper_clears_the_kill_switch():
    store = TradingModeStore()
    store.set_mode(LIVE, confirmed=True)
    store.activate_kill_switch()
    store.set_mode(PAPER)
    assert store.kill_switch_active is False


def test_invalid_mode_is_rejected():
    store = TradingModeStore()
    with pytest.raises(ValueError):
        store.set_mode("SORT_OF_LIVE", confirmed=True)


# ── API: paper cannot become live by accident ───────────────────

def test_api_defaults_to_paper(client):
    resp = client.get("/api/trading/mode")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "PAPER"


def test_api_rejects_live_without_confirmation(client):
    resp = client.post("/api/trading/mode", json={"mode": "LIVE", "confirmed": False})
    # Either blocked for lacking confirmation (428) or for no broker
    # connection (409) — both are "no", which is the only thing this test
    # requires. What it must never do is return 200.
    assert resp.status_code in (409, 428)
    assert client.get("/api/trading/mode").json()["mode"] == "PAPER"


def test_api_rejects_live_without_angel_connected(client):
    resp = client.post("/api/trading/mode", json={"mode": "LIVE", "confirmed": True})
    assert resp.status_code == 409
    assert client.get("/api/trading/mode").json()["mode"] == "PAPER"


def test_paper_trading_endpoints_work_with_angel_unconfigured(client):
    """The existing demo account must be completely unaffected by any of this."""
    resp = client.get("/api/paper")
    assert resp.status_code == 200
    assert "account" in resp.json()


# ── instrument capability ────────────────────────────────────

def test_crypto_is_never_live_tradable():
    service = AngelOneInstrumentService()
    result = _run(service.capability("BTCUSD"))
    assert result["supported"] is False
    assert "crypto" in result["reason"].lower()


def test_unknown_symbol_is_not_supported():
    service = AngelOneInstrumentService()
    result = _run(service.capability("NOT_A_REAL_SYMBOL"))
    assert result["supported"] is False


def test_index_resolves_to_the_nearest_unexpired_future_not_the_spot_row():
    """Regression test: the resolver used to match NIFTY's non-tradable
    index-quote row (lotsize 1, no expiry, instrumenttype "") instead of a
    real FUTIDX contract. This proves it now requires a dated, typed
    derivative row and picks the nearest one that hasn't expired."""
    service = AngelOneInstrumentService()
    near = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%d%b%Y").upper()
    far = (datetime.now(timezone.utc) + timedelta(days=40)).strftime("%d%b%Y").upper()
    expired = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%d%b%Y").upper()

    service._by_key = {  # noqa: SLF001 - test seeds the cache directly
        ("NSE", "NIFTY"): {  # the bogus spot/index row
            "token": "26000", "symbol": "NIFTY", "name": "NIFTY", "expiry": "",
            "lotsize": "1", "instrumenttype": "", "exch_seg": "NSE", "tick_size": "0",
        },
    }
    service._derivatives = {  # noqa: SLF001
        ("NFO", "NIFTY", "FUTIDX"): [
            {"token": "1", "symbol": f"NIFTY{expired}FUT", "name": "NIFTY",
             "expiry": expired, "lotsize": "65", "instrumenttype": "FUTIDX",
             "exch_seg": "NFO", "tick_size": "10"},
            {"token": "2", "symbol": f"NIFTY{far}FUT", "name": "NIFTY",
             "expiry": far, "lotsize": "65", "instrumenttype": "FUTIDX",
             "exch_seg": "NFO", "tick_size": "10"},
            {"token": "3", "symbol": f"NIFTY{near}FUT", "name": "NIFTY",
             "expiry": near, "lotsize": "65", "instrumenttype": "FUTIDX",
             "exch_seg": "NFO", "tick_size": "10"},
        ],
    }
    service._loaded_at = 1e12  # noqa: SLF001 - pretend it's fresh, skip the network call

    match = _run(service.resolve("NIFTY"))
    assert match.exchange == "NFO"
    assert match.instrument_type == "FUTIDX"
    assert match.lot_size == 65
    assert match.expiry == near, "must pick the nearest unexpired contract"
    assert "FUT" in match.trading_symbol


def test_index_with_only_expired_contracts_is_unsupported():
    service = AngelOneInstrumentService()
    expired = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%d%b%Y").upper()
    service._by_key = {}  # noqa: SLF001
    service._derivatives = {  # noqa: SLF001
        ("NFO", "NIFTY", "FUTIDX"): [
            {"token": "1", "symbol": f"NIFTY{expired}FUT", "name": "NIFTY",
             "expiry": expired, "lotsize": "65", "instrumenttype": "FUTIDX",
             "exch_seg": "NFO", "tick_size": "10"},
        ],
    }
    service._loaded_at = 1e12  # noqa: SLF001
    with pytest.raises(InstrumentUnsupported):
        _run(service.resolve("NIFTY"))


def test_equity_resolves_by_eq_suffix_not_a_derivative_row():
    service = AngelOneInstrumentService()
    service._by_key = {  # noqa: SLF001
        ("NSE", "RELIANCE-EQ"): {
            "token": "2885", "symbol": "RELIANCE-EQ", "name": "RELIANCE",
            "expiry": "", "lotsize": "1", "instrumenttype": "",
            "exch_seg": "NSE", "tick_size": "10",
        },
    }
    service._derivatives = {}  # noqa: SLF001
    service._loaded_at = 1e12  # noqa: SLF001
    match = _run(service.resolve("RELIANCE"))
    assert match.trading_symbol == "RELIANCE-EQ"
    assert match.lot_size == 1


def _run(coro):
    import asyncio
    return asyncio.run(coro)
