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

from app import instruments, stocks
from app.broker.angelone import instruments as instruments_module
from app.broker.angelone.auth import angel_auth
from app.broker.angelone.instruments import (
    AngelOneInstrumentService, InstrumentUnsupported,
)
from app.broker.live_limits import sector_of
from app.broker.trading_mode import LIVE, PAPER, TradingModeStore
from app.data.providers import resolve_provider
from app.instruments import get_instrument
from app.main import app
from conftest import SHIPPED_SYMBOLS, angel_only_providers


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


def test_changing_mode_does_not_clear_the_kill_switch():
    """It used to: PAPER and back to LIVE was a two-click way around an
    emergency stop. Only an explicit clear lifts it."""
    store = TradingModeStore()
    store.set_mode(LIVE, confirmed=True)
    store.activate_kill_switch("Daily loss limit reached.")
    store.set_mode(PAPER)
    assert store.kill_switch_active is True
    store.set_mode(LIVE, confirmed=True)
    assert store.kill_switch_active is True
    assert store.can_trade_live() is False
    assert store.status()["kill_switch_reason"] == "Daily loss limit reached."

    store.deactivate_kill_switch()
    assert store.can_trade_live() is True
    assert store.status()["kill_switch_reason"] is None


def test_kill_switch_survives_a_restart_but_the_mode_does_not(tmp_path):
    path = tmp_path / "kill_switch.json"
    first = TradingModeStore(state_path=path)
    first.set_mode(LIVE, confirmed=True)
    first.activate_kill_switch("Stop could not be placed.")

    restarted = TradingModeStore(state_path=path)
    assert restarted.mode == PAPER, "a restart must never resume LIVE"
    assert restarted.kill_switch_active is True
    assert restarted.kill_switch_reason == "Stop could not be placed."
    assert restarted.status()["kill_switch_activated_at"] is not None

    restarted.deactivate_kill_switch()
    assert TradingModeStore(state_path=path).kill_switch_active is False


def test_unreadable_kill_switch_state_fails_towards_no_trading(tmp_path):
    path = tmp_path / "kill_switch.json"
    path.write_text("{ this is not json")
    store = TradingModeStore(state_path=path)
    assert store.kill_switch_active is True
    store.set_mode(LIVE, confirmed=True)
    assert store.can_trade_live() is False


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


def test_resolve_index_spot_gives_the_continuous_index_row_not_the_future():
    """A chart should show NIFTY, not whichever month's future is nearest
    expiry — the future carries its own basis and only ever has a few weeks
    of real history. `resolve_index_spot` is a second, data-only lookup;
    `resolve()` (above) still gives the tradable future, unchanged — a live
    order can never be placed on an index itself."""
    service = AngelOneInstrumentService()
    near = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%d%b%Y").upper()
    service._derivatives = {  # noqa: SLF001
        ("NFO", "NIFTY", "FUTIDX"): [
            {"token": "3", "symbol": f"NIFTY{near}FUT", "name": "NIFTY",
             "expiry": near, "lotsize": "65", "instrumenttype": "FUTIDX",
             "exch_seg": "NFO", "tick_size": "10"},
        ],
    }
    service._index_spot = {  # noqa: SLF001
        "NIFTY": {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY",
                  "instrumenttype": "AMXIDX", "exch_seg": "NSE",
                  "lotsize": "1", "tick_size": "0"},
    }
    service._loaded_at = 1e12  # noqa: SLF001

    spot = _run(service.resolve_index_spot("NIFTY"))
    assert spot.exchange == "NSE"
    assert spot.token == "99926000"
    assert spot.instrument_type == "AMXIDX"

    future = _run(service.resolve("NIFTY"))
    assert future.exchange == "NFO"
    assert future.instrument_type == "FUTIDX"


def test_resolve_index_spot_is_none_when_the_scrip_master_has_no_spot_row():
    service = AngelOneInstrumentService()
    service._index_spot = {}  # noqa: SLF001 - e.g. before the master has loaded it
    service._loaded_at = 1e12  # noqa: SLF001
    assert _run(service.resolve_index_spot("NIFTY")) is None


def test_resolve_index_spot_is_none_for_a_non_index_instrument():
    service = AngelOneInstrumentService()
    service._index_spot = {"NIFTY": {"token": "99926000"}}  # noqa: SLF001
    service._loaded_at = 1e12  # noqa: SLF001
    assert _run(service.resolve_index_spot("RELIANCE")) is None


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


def test_a_market_with_no_indian_exchange_is_unsupported():
    result = _run(AngelOneInstrumentService().capability("EURUSD"))
    assert result["supported"] is False
    assert "no Indian exchange mapping" in result["reason"]


# ── what there is to trade ────────────────────────────────────

def _day(offset):
    return (datetime.now(timezone.utc) + timedelta(days=offset)).strftime("%d%b%Y").upper()


def _future(name, expiry, lot):
    return {"token": "1", "symbol": name + expiry + "FUT", "name": name, "expiry": expiry,
            "lotsize": str(lot), "instrumenttype": "FUTSTK", "exch_seg": "NFO",
            "tick_size": "5"}


def _share(name, tick):
    return {"token": "2", "symbol": name + "-EQ", "name": name, "expiry": "",
            "lotsize": "1", "instrumenttype": "", "exch_seg": "NSE", "tick_size": str(tick)}


def test_the_application_lists_nothing_angel_one_cannot_trade():
    assert set(SHIPPED_SYMBOLS) == {"USDINR", "EURINR", "GBPINR", "JPYINR", "NIFTY",
                                    "BANKNIFTY", "MCXGOLD", "MCXSILVER", "MCXCRUDE"}
    for symbol in SHIPPED_SYMBOLS:
        instrument = get_instrument(symbol)
        assert instrument.tradable_in_india and instrument.venue in ("NFO", "CDS", "MCX")
        assert instrument.asset_class != "stock", "no stock is written down: Angel One lists them"


def test_the_stocks_are_the_ones_angel_one_lists_with_its_lot_and_tick(universe):
    service = AngelOneInstrumentService()
    near, far, gone = _day(10), _day(40), _day(-5)
    service._derivatives = {  # noqa: SLF001 - test seeds the cache directly
        ("NFO", "RELIANCE", "FUTSTK"): [_future("RELIANCE", far, 999),
                                        _future("RELIANCE", near, 250),
                                        _future("RELIANCE", gone, 500)],
        ("NFO", "NEWLISTING", "FUTSTK"): [_future("NEWLISTING", near, 1200)],
        ("NFO", "DELISTED", "FUTSTK"): [_future("DELISTED", gone, 100)],
        ("NFO", "NOSHARE", "FUTSTK"): [_future("NOSHARE", near, 100)],
        ("NFO", "BADROW", "FUTSTK"): [_future("BADROW", near, "n/a")],
        ("NFO", "011NSETEST", "FUTSTK"): [_future("011NSETEST", near, 50)],
        ("NFO", "NIFTY", "FUTIDX"): [
            dict(_future("NIFTY", near, 65), instrumenttype="FUTIDX", tick_size="10")],
    }
    service._by_key = {  # noqa: SLF001
        ("NSE", "RELIANCE-EQ"): _share("RELIANCE", 10), ("NSE", "NEWLISTING-EQ"): _share("NEWLISTING", 5),
        ("NSE", "DELISTED-EQ"): _share("DELISTED", 5), ("NSE", "BADROW-EQ"): _share("BADROW", 5),
        ("NSE", "011NSETEST-EQ"): _share("011NSETEST", 5),
    }
    listed = {listing.symbol: listing for listing in service.stock_universe()}
    assert set(listed) == {"RELIANCE", "NEWLISTING"}, \
        "an expired future, a missing share, an unreadable lot or the exchange's own " \
        "test scrip is not a listing"
    assert (listed["RELIANCE"].lot_size, listed["RELIANCE"].tick_size) == (250, 0.1), \
        "the nearest contract that has not expired, and the share's own tick"

    service._update_stock_universe()  # noqa: SLF001
    reliance = get_instrument("RELIANCE")
    assert (reliance.contract_size, reliance.pip_size) == (250, 0.1), "not the 500 it had"
    assert reliance.display == "Reliance Industries" and "Energy" in reliance.note
    new = get_instrument("NEWLISTING")
    assert (new.display, new.contract_size, new.pip_size) == ("NEWLISTING", 1200, 0.05)
    with pytest.raises(KeyError):
        get_instrument("TCS")       # Angel One no longer lists it, so neither does this
    assert "RELIANCE" in instruments.DEFAULT_STREAM_SYMBOLS
    assert "NIFTY" in instruments.SYMBOLS and "HDFCBANK" not in instruments.SYMBOLS

    # The index future's lot is revised too, and the one written down was stale.
    nifty = get_instrument("NIFTY")
    assert (nifty.contract_size, nifty.pip_size) == (65, 0.1)
    assert get_instrument("BANKNIFTY").contract_size == 30, "no contract seen: left as written"
    assert get_instrument("USDINR").contract_size == 1_000, \
        "a currency contract is counted in lots there, not units: never adopted"


def test_the_universe_is_kept_for_the_next_start(universe, monkeypatch, tmp_path):
    """A restart has no broker session. A saved trade on a stock still has to
    resolve, so what Angel One last listed is kept and read back."""
    monkeypatch.setattr(instruments, "UNIVERSE_PATH", tmp_path / "angel_universe.json")
    stocks.set_universe([stocks.Listing("WIPRO", 3000, 0.01), stocks.Listing("M&M", 200, 0.1)])
    instruments.refresh_stock_instruments()
    instruments.adopt_contract("NIFTY", 65, 0.1)
    instruments.save_universe()

    stocks.set_universe([])                         # …the process ends, and another starts
    instruments.refresh_stock_instruments()
    instruments.adopt_contract("NIFTY", 75, 0.05)
    with pytest.raises(KeyError):
        get_instrument("WIPRO")

    instruments.restore_universe()
    assert (get_instrument("WIPRO").contract_size, get_instrument("WIPRO").pip_size) == (3000, 0.01)
    assert get_instrument("M&M").display == "Mahindra & Mahindra"
    assert (get_instrument("NIFTY").contract_size, get_instrument("NIFTY").pip_size) == (65, 0.1)

    (tmp_path / "angel_universe.json").write_text("{ not json")
    instruments.restore_universe()                  # unreadable: ignored, nothing lost
    assert get_instrument("WIPRO").contract_size == 3000
    (tmp_path / "angel_universe.json").unlink()
    instruments.restore_universe()                  # absent: a first run, and fine


def test_a_master_with_no_stock_futures_is_a_bad_read_not_an_empty_market(universe):
    service = AngelOneInstrumentService()
    service._derivatives, service._by_key = {}, {}  # noqa: SLF001
    service._update_stock_universe()  # noqa: SLF001
    assert get_instrument("RELIANCE").contract_size == 500, "the last good universe stands"


def test_loading_the_scrip_master_adopts_its_stocks(universe, monkeypatch):
    rows = [_share("RELIANCE", 10), _future("RELIANCE", _day(10), 250),
            _share("WIPRO", 5), _future("WIPRO", _day(10), 3000),
            dict(_share("ELSEWHERE", 5), exch_seg="NCDEX"),
            {"token": "9", "symbol": "GOLD05DEC26FUT", "name": "GOLD", "expiry": _day(30),
             "lotsize": "1", "instrumenttype": "FUTCOM", "exch_seg": "MCX", "tick_size": "100"}]

    class Master:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            assert url == instruments_module.SCRIP_MASTER_URL
            return type("Response", (), {"raise_for_status": lambda self: None,
                                         "json": lambda self: rows})()

    monkeypatch.setattr(instruments_module.httpx, "AsyncClient", lambda timeout=None: Master())
    service = AngelOneInstrumentService()
    _run(service.ensure_loaded())
    assert [listing.symbol for listing in stocks.UNIVERSE] == ["RELIANCE", "WIPRO"]
    assert get_instrument("WIPRO").contract_size == 3000
    assert get_instrument("WIPRO").display == "Wipro"
    assert _run(service.resolve("MCXGOLD")).exchange == "MCX", "and the futures still resolve"


def test_sectors_are_a_label_and_an_unlabelled_stock_is_still_counted(universe):
    assert sector_of("HDFCBANK") == sector_of("SBIN") == "Financials"
    assert sector_of("INFY") == "IT"
    assert sector_of("NIFTY") is None and sector_of("USDINR") is None, "not a stock"
    stocks.set_universe(list(stocks.UNIVERSE) + [stocks.Listing("NEWLISTING", 100, 0.05)])
    assert sector_of("NEWLISTING") == stocks.UNCLASSIFIED, \
        "bound together with the other unlabelled ones, rather than by no limit at all"


# ── where prices come from ────────────────────────────────────

def test_angel_one_is_the_only_source_and_only_while_connected(monkeypatch):
    assert angel_only_providers() == {}, "no session: nothing serves anything"
    assert resolve_provider("RELIANCE", {}) is None

    monkeypatch.setattr(type(angel_auth), "connected", property(lambda self: True))
    providers = angel_only_providers()
    assert list(providers) == ["angelone"]
    assert resolve_provider("RELIANCE", providers).name == "angelone"
    assert resolve_provider("MCXCRUDE", providers).live is True
    assert resolve_provider("EURUSD", providers) is None, "not something Angel One trades"
    assert resolve_provider("NOPE", providers) is None


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ── the SDK must not write credentials to its log ─────────────

def test_sdk_request_logs_never_contain_credentials():
    """SmartApi logs the whole request — headers and body — whenever one
    fails. These are its two real message formats, with the values a failed
    login and a failed order would carry."""
    import logging

    import logzero

    from app.broker.angelone.auth import _load_sdk

    _load_sdk()   # installs the redaction; no network, no login

    captured = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = Capture()
    logzero.logger.addHandler(handler)
    try:
        headers = {
            "Content-type": "application/json", "X-ClientLocalIP": "127.0.0.1",
            "X-PrivateKey": "APIKEY-9f3c", "X-UserType": "USER",
            "Authorization": "Bearer eyJhbGciOiJIUzUxMiJ9.payload.sig-77",
        }
        login = {"clientcode": "K123456", "password": "hunter2's \"pin\"", "totp": "481516"}
        url = "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword"
        logzero.logger.error(
            f"Error occurred while making a POST request to {url}. Headers: {headers}, "
            f"Request: {login}, Response: HTTPSConnectionPool: Read timed out.")
        data = {"status": False, "message": "Invalid totp", "errorcode": "AB1050",
                "data": {"jwtToken": "eyJraWQiOiJ0b2tlbg.x.y", "refreshToken": "rt-secret-1",
                         "feedToken": "ft-secret-2"}}
        logzero.logger.error(
            f"Error occurred while making a POST request to {url}. Error: {data['message']}. "
            f"URL: {url}, Headers: {headers}, Request: {login}, Response: {data}")
    finally:
        logzero.logger.removeHandler(handler)

    assert len(captured) == 2
    for line in captured:
        for secret in ("hunter2", "pin", "481516", "APIKEY-9f3c", "eyJhbGci", "sig-77",
                       "K123456", "eyJraWQi", "rt-secret-1", "ft-secret-2"):
            assert secret not in line, "leaked {!r} in: {}".format(secret, line)
        # What makes the failure diagnosable survives.
        assert "loginByPassword" in line
    assert "Read timed out" in captured[0]
    assert "Invalid totp" in captured[1]
    assert "AB1050" in captured[1]
