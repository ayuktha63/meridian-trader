from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_subsystems(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["database"] in ("sqlite", "postgres")
    assert body["cache"] in ("memory", "redis")
    assert "NIFTY" in body["symbols"]
    assert "analyst" in body
    assert "risk-free" in body["disclaimer"]


def test_symbol_and_timeframe_catalogue(client):
    symbols = client.get("/api/market/symbols").json()
    codes = {s["symbol"] for s in symbols}
    assert {"USDINR", "EURINR", "GBPINR", "JPYINR", "NIFTY", "BANKNIFTY",
            "MCXGOLD", "MCXSILVER", "MCXCRUDE", "RELIANCE"} <= codes
    usdinr = next(s for s in symbols if s["symbol"] == "USDINR")
    assert usdinr["pip_size"] == 0.0025 and usdinr["digits"] == 4
    reliance = next(s for s in symbols if s["symbol"] == "RELIANCE")
    assert reliance["contract_size"] == 500, "the lot the universe was given, not a table's"

    timeframes = {t["code"] for t in client.get("/api/market/timeframes").json()}
    assert {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"} <= timeframes


def test_candles_are_ordered_and_consistent(client):
    body = client.get("/api/market/candles",
                      params={"symbol": "EURUSD", "timeframe": "15m", "limit": 200}).json()
    candles = body["candles"]
    assert len(candles) > 100
    times = [c["time"] for c in candles]
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    for candle in candles:
        assert candle["high"] >= max(candle["open"], candle["close"])
        assert candle["low"] <= min(candle["open"], candle["close"])


def test_a_chart_with_no_broker_session_is_empty_and_says_why(client, monkeypatch):
    from app.data import feed as feed_module
    from app.data.store import store

    monkeypatch.setattr(feed_module.feed, "providers", {})
    monkeypatch.setattr(feed_module, "build_providers", dict)
    store.series("MCXCRUDE", "30m").replace([])
    response = client.get("/api/market/candles",
                          params={"symbol": "MCXCRUDE", "timeframe": "30m"})
    assert response.status_code == 503
    assert response.json()["detail"] == "No data for MCXCRUDE 30m: Angel One is not connected."


def test_settings_name_angel_one_as_the_only_source(client):
    body = client.get("/api/settings").json()
    assert [provider["id"] for provider in body["providers"]] == ["angelone"]
    assert body["providers"][0]["ready"] is False, "no session in a test"
    assert not {"market_provider", "hybrid_crypto_live", "twelvedata_api_key",
                "zerodha_api_key"} & set(body["settings"])
    listed = {row["symbol"]: row for row in body["instruments"]}
    assert listed["RELIANCE"]["lot_size"] == 500 and listed["NIFTY"]["venue"] == "NFO"


def test_unknown_symbol_is_404(client):
    assert client.get("/api/market/candles",
                      params={"symbol": "NOPE", "timeframe": "15m"}).status_code == 404


def test_market_clock(client):
    body = client.get("/api/market/clock").json()
    assert "utc_time" in body
    assert body["liquidity_regime"] in ("closed", "normal", "high")
    assert [session["name"] for session in body["sessions"]] == ["NSE", "CDS", "MCX"]
    assert body["ist_time"].endswith("IST") and "Holidays" in body["basis"]


def test_drawing_lifecycle_and_analytics(client):
    payload = {
        "symbol": "EURUSD",
        "timeframe": "15m",
        "type": "resistance",
        "points": [{"time": 1700000000, "price": 1.1000},
                   {"time": 1700900000, "price": 1.1000}],
        "label": "Weekly resistance",
    }
    created = client.post("/api/drawings", json=payload)
    assert created.status_code == 201
    drawing = created.json()
    assert drawing["id"] and drawing["type"] == "resistance"

    listed = client.get("/api/drawings", params={"symbol": "EURUSD"}).json()
    assert any(d["id"] == drawing["id"] for d in listed)

    patched = client.patch("/api/drawings/{}".format(drawing["id"]),
                           json={"label": "Renamed"}).json()
    assert patched["label"] == "Renamed"

    analytics = client.get("/api/drawings/analytics",
                           params={"symbol": "EURUSD", "timeframe": "15m"})
    assert analytics.status_code == 200
    for item in analytics.json():
        assert item["role"] in ("resistance", "support", "zone", "supply",
                                "demand", "measurement", "annotation", "neutral")
        assert 0 <= item["strength"] <= 100

    assert client.delete("/api/drawings/{}".format(drawing["id"])).status_code == 204
    assert client.delete("/api/drawings/{}".format(drawing["id"])).status_code == 404


def test_analysis_returns_full_payload(client):
    response = client.post("/api/analysis/run", json={
        "symbol": "EURUSD", "timeframe": "15m",
        "include_ai": False, "force_refresh": True,
    })
    assert response.status_code == 200
    body = response.json()

    assert body["symbol"] == "EURUSD"
    assert body["price"] > 0
    assert body["verdict"]["decision"] in ("BUY", "SELL", "WAIT")
    assert 0 <= body["confidence"]["final_confidence"] <= 100
    assert len(body["confidence"]["components"]) == 6
    assert body["narrative"]
    assert "market_structure" in body["evidence"]
    assert "multi_timeframe" in body["evidence"]
    assert isinstance(body["overlays"], list)

    # The analyst must always show both sides of the argument.
    verdict = body["verdict"]
    assert verdict["summary"]
    assert verdict["uncertainty"]

    # No decision may ship without an invalidation level.
    if verdict["decision"] != "WAIT":
        assert body["plan"]["stop_loss"] is not None
        assert body["plan"]["risk_pips"] > 0


def test_analysis_is_cached_between_identical_requests(client):
    first = client.post("/api/analysis/run", json={
        "symbol": "NIFTY", "timeframe": "1h",
        "include_ai": False, "force_refresh": True}).json()
    second = client.post("/api/analysis/run", json={
        "symbol": "NIFTY", "timeframe": "1h",
        "include_ai": False, "force_refresh": False}).json()
    assert first["id"] == second["id"]


def test_watchlist_covers_requested_symbols(client):
    body = client.get("/api/analysis/watchlist",
                      params={"timeframe": "1h", "symbols": "TATAMOTORS,USDINR"}).json()
    assert {row["symbol"] for row in body} == {"TATAMOTORS", "USDINR"}
    for row in body:
        assert row["bias"] in ("bullish", "bearish", "neutral")


def test_ai_status_reports_fallback(client):
    body = client.get("/api/analysis/ai-status").json()
    assert "enabled" in body
    assert body["fallback"] == "deterministic confluence engine"


def test_journal_roundtrip_and_stats(client):
    created = client.post("/api/journal", json={
        "symbol": "EURUSD", "timeframe": "15m", "decision": "BUY",
        "confidence": 72.0, "probability": 58.0,
        "entry": 1.1000, "stop_loss": 1.0960, "take_profit": 1.1080,
        "risk_reward": 2.0, "summary": "Test entry",
    })
    assert created.status_code == 201
    entry_id = created.json()["id"]

    closed = client.patch("/api/journal/{}/outcome".format(entry_id),
                          json={"outcome": "win", "exit_price": 1.1080}).json()
    assert closed["outcome"] == "win"
    assert closed["r_multiple"] == pytest.approx(2.0, abs=0.05)

    stats = client.get("/api/journal/stats").json()
    assert stats["total"] >= 1
    assert stats["wins"] >= 1
    assert "accuracy_by_confidence" in stats

    assert client.delete("/api/journal/{}".format(entry_id)).status_code == 204


def test_backtest_endpoint(client):
    body = client.post("/api/backtest/run", json={
        "symbol": "EURUSD", "timeframe": "1h", "bars": 120,
        "min_confidence": 55, "risk_reward": 2.0,
    }).json()
    assert body["strategy"] == "engine"
    assert body["bars_tested"] == 120
    assert body["metrics"]["trades"] >= 0
    assert body["notes"]
    assert body["edge"]["method"] == "holdout"
    assert body["edge"]["passed"] in (True, False)


def test_websocket_subscribe_and_stream(client):
    with client.websocket_connect("/ws/stream") as socket:
        hello = socket.receive_json()
        assert hello["type"] == "hello"
        assert "EURUSD" in hello["symbols"]

        socket.send_json({"type": "subscribe", "symbol": "EURUSD", "timeframe": "5m"})
        for _ in range(10):
            message = socket.receive_json()
            if message["type"] == "subscribed":
                assert message["symbol"] == "EURUSD"
                assert message["timeframe"] == "5m"
                break
        else:
            pytest.fail("never received a subscription acknowledgement")

        socket.send_json({"type": "ping"})
        for _ in range(20):
            message = socket.receive_json()
            if message["type"] == "pong":
                break
        else:
            pytest.fail("never received a pong")


def test_websocket_rejects_unknown_symbol(client):
    with client.websocket_connect("/ws/stream") as socket:
        socket.receive_json()          # hello
        socket.send_json({"type": "subscribe", "symbol": "FAKEPAIR", "timeframe": "5m"})
        # Drain rather than asserting on the very next frame: unrelated pushes
        # (the demo-account snapshot, quotes) legitimately arrive in between,
        # and message ordering between independent streams is not a contract.
        for _ in range(10):
            message = socket.receive_json()
            if message["type"] == "error":
                break
        else:
            pytest.fail("unknown symbol was not rejected")


# ── risk management ───────────────────────────────────────────

def _open(client, **kwargs):
    payload = {"symbol": "EURUSD", "timeframe": "15m", "side": "LONG",
               "entry": 1.1000, "stop_loss": 1.0900, "lots": 1.0}
    payload.update(kwargs)
    return client.post("/api/positions", json=payload)


def _cleanup(client):
    for position in client.get("/api/positions", params={"status": "open"}).json():
        client.delete("/api/positions/{}".format(position["id"]))


def test_opening_a_position_captures_the_risk_unit(client):
    _cleanup(client)
    body = _open(client).json()
    assert body["initial_stop"] == 1.0900
    # The broker is assumed to hold the entry stop until told otherwise.
    assert body["broker_stop"] == 1.0900
    _cleanup(client)


def test_the_risk_unit_survives_the_stop_being_trailed(client):
    """The whole point of persisting it: R must stay meaningful after
    protection is taken."""
    _cleanup(client)
    position = _open(client).json()
    client.patch("/api/positions/{}".format(position["id"]),
                 json={"stop_loss": 1.1000})
    after = client.get("/api/positions", params={"status": "open"}).json()[0]
    assert after["stop_loss"] == 1.1000
    assert after["initial_stop"] == 1.0900, "the risk unit moved with the stop"
    _cleanup(client)


def test_a_stop_can_be_tightened_but_never_widened(client):
    _cleanup(client)
    position = _open(client).json()
    pid = position["id"]

    client.patch("/api/positions/{}".format(pid), json={"stop_loss": 1.0950})
    assert client.get("/api/positions").json()[0]["stop_loss"] == 1.0950

    # Widening is silently refused rather than erroring, so an automated
    # caller cannot loosen protection by retrying.
    client.patch("/api/positions/{}".format(pid), json={"stop_loss": 1.0800})
    assert client.get("/api/positions").json()[0]["stop_loss"] == 1.0950
    _cleanup(client)


def test_realised_r_is_measured_against_the_original_stop(client):
    """A trade trailed to breakeven and then closed in profit must not journal
    as 0R — that would corrupt the whole calibration table."""
    _cleanup(client)
    position = _open(client).json()
    pid = position["id"]
    client.patch("/api/positions/{}".format(pid), json={"stop_loss": 1.1000})
    closed = client.post("/api/positions/{}/close".format(pid),
                         json={"exit_price": 1.1300}).json()
    # 300 pips of gain against a 100-pip risk unit.
    assert closed["realised_r"] == pytest.approx(3.0, abs=0.01)
    _cleanup(client)


def test_confirming_the_broker_stop_clears_the_warning(client):
    _cleanup(client)
    position = _open(client).json()
    pid = position["id"]
    client.patch("/api/positions/{}".format(pid), json={"stop_loss": 1.0950})
    assert client.get("/api/positions").json()[0]["broker_stop"] == 1.0900

    client.patch("/api/positions/{}".format(pid), json={"broker_stop": 1.0950})
    assert client.get("/api/positions").json()[0]["broker_stop"] == 1.0950
    _cleanup(client)


def test_analysis_always_carries_account_risk(client):
    """The guardrails matter most when there is *no* position and the trader is
    about to open one, so they are never conditional on having one."""
    body = client.post("/api/analysis/run",
                       json={"symbol": "EURUSD", "timeframe": "15m",
                             "include_ai": False}).json()
    assert body["account_risk"] is not None
    assert body["account_risk"]["status"] in ("clear", "caution", "halt")
    assert len(body["account_risk"]["guardrails"]) == 4


# ── what the broker holds ─────────────────────────────────────

def _broker_row(**changes):
    row = {"tradingsymbol": "INFY-EQ", "symbolname": "INFY", "exchange": "NSE",
           "producttype": "INTRADAY", "netqty": "1", "buyavgprice": "1050.5",
           "sellavgprice": "0.0", "ltp": "1057.75", "pnl": "7.25"}
    row.update(changes)
    return row


def _stop_order(**changes):
    row = {"orderid": "A1", "tradingsymbol": "INFY-EQ", "exchange": "NSE", "transactiontype": "SELL",
           "ordertype": "STOPLOSS_MARKET", "producttype": "INTRADAY", "quantity": "1",
           "filledshares": "0", "price": 0.0, "triggerprice": 1040.0, "averageprice": 0.0,
           "orderstatus": "trigger pending", "text": "", "updatetime": "13-Oct-2026 12:30:00"}
    row.update(changes)
    return row


def _broker(monkeypatch, positions, orders=None, connected=True):
    import asyncio

    from app.broker import angel_book
    from app.cache import cache

    async def get_positions():
        if isinstance(positions, Exception):
            raise positions
        return positions

    async def get_orders():
        return orders or []

    asyncio.run(cache.set_json(angel_book.CACHE_KEY, None, ttl=1))
    monkeypatch.setattr(type(angel_book.angel_auth), "connected", property(lambda self: connected))
    monkeypatch.setattr(angel_book.angel_broker, "get_positions", get_positions)
    monkeypatch.setattr(angel_book.angel_broker, "get_orders", get_orders)


def test_a_position_taken_in_the_brokers_own_app_is_shown(client, monkeypatch):
    """The account holds more than Meridian placed, and a position it cannot
    see is one nobody is watching."""
    _broker(monkeypatch, [_broker_row(), _broker_row(tradingsymbol="SBIN-EQ", symbolname="SBIN",
                                                      netqty="0")], [_stop_order()])
    book = client.get("/api/trades/live/book").json()
    held, = book["positions"]
    assert (held["symbol"], held["chart_symbol"], held["net_quantity"]) == ("INFY", "INFY", 1.0)
    assert held["buy_average"] == 1050.5 and held["pnl"] == 7.25, "a closed position is not listed"
    stop, = book["orders"]
    assert (stop["chart_symbol"], stop["side"], stop["trigger_price"], stop["working"]) == (
        "INFY", "SELL", 1040.0, True)
    overview = client.get("/api/trades/live/overview").json()
    assert [p["symbol"] for p in overview["broker_positions"]] == ["INFY"]


def test_finished_orders_are_not_working_ones(client, monkeypatch):
    _broker(monkeypatch, [], [_stop_order(), _stop_order(orderid="A2", orderstatus="rejected"),
                              _stop_order(orderid="A3", orderstatus="complete")])
    working = {o["order_id"]: o["working"] for o in client.get("/api/trades/live/book").json()["orders"]}
    assert working == {"A1": True, "A2": False, "A3": False}


def test_an_unreadable_book_is_not_an_empty_one(client, monkeypatch):
    _broker(monkeypatch, RuntimeError("Access denied because of exceeding access rate"))
    assert client.get("/api/trades/live/book").status_code == 503
    assert client.get("/api/trades/live/overview").json()["broker_positions"] is None


def test_without_a_broker_session_there_is_no_book(client, monkeypatch):
    _broker(monkeypatch, [_broker_row()], connected=False)
    assert client.get("/api/trades/live/book").status_code == 503


def test_todays_charges_are_estimated_from_the_orders_that_filled(client, monkeypatch):
    """A rejected or still-working order has paid nothing; a filled one has
    paid brokerage with its floor, and the sell side its STT."""
    buy = _stop_order(orderid="B1", transactiontype="BUY", ordertype="MARKET", triggerprice=0.0,
                      price=1050.5, averageprice=1050.5, filledshares="1", orderstatus="complete")
    sell = _stop_order(orderid="B2", transactiontype="SELL", ordertype="MARKET", triggerprice=0.0,
                       price=1057.75, averageprice=1057.75, filledshares="1",
                       orderstatus="complete")
    _broker(monkeypatch, [_broker_row(realised="7.25", unrealised="0.0", netqty="0")],
            [buy, sell, _stop_order(orderid="B3", orderstatus="rejected"), _stop_order(orderid="B4")])
    book = client.get("/api/trades/live/book").json()
    assert book["realised_today"] == 7.25
    assert 12.0 < book["charges_today"] < 12.4, "₹5 floor each way, GST, STT on the sell"


def test_the_brokers_own_debits_are_shown_beside_the_estimate(client, monkeypatch):
    _broker(monkeypatch, [_broker_row(realised="-12.5", unrealised="0.0", netqty="0")], [])

    async def funds():
        return {"utiliseddebits": "17.9114", "availablecash": "420.4886"}
    monkeypatch.setattr(__import__("app.broker.angel_book", fromlist=["x"]).angel_broker,
                        "get_funds", funds)
    book = client.get("/api/trades/live/book").json()
    assert book["broker_debits"] == 17.91 and book["realised_today"] == -12.5


def test_unreadable_funds_leave_the_debits_unknown_not_zero(client, monkeypatch):
    _broker(monkeypatch, [_broker_row()], [])

    async def funds():
        raise RuntimeError("Access denied because of exceeding access rate")
    monkeypatch.setattr(__import__("app.broker.angel_book", fromlist=["x"]).angel_broker,
                        "get_funds", funds)
    assert client.get("/api/trades/live/book").json()["broker_debits"] is None
