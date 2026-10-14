"""Logs a machine can read, a readiness answer, metrics, and the table of
state changes a live execution may make."""
from __future__ import annotations

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app import alerts, observability
from app.broker import order_states
from app.broker.order_states import IllegalTransition
from app.db.models import LiveExecutionRow
from app.db.session import session_scope
from app.main import app
from app.schemas.execution import ExecutionStatus as S

REMOTE = ("203.0.113.9", 50000)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# ── logs ──────────────────────────────────────────────────────

def _record(message, **extra):
    record = logging.LogRecord("meridian.test", logging.WARNING, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_a_log_line_is_one_json_object_with_its_context():
    line = observability.JsonFormatter().format(_record("stop placed", symbol="RELIANCE",
                                                        quantity=10))
    entry = json.loads(line)
    assert (entry["level"], entry["logger"], entry["message"]) == (
        "WARNING", "meridian.test", "stop placed")
    assert (entry["symbol"], entry["quantity"]) == ("RELIANCE", 10)
    assert entry["time"].endswith("Z") and "\n" not in line


def test_secrets_do_not_reach_a_json_log_either():
    line = observability.JsonFormatter().format(
        _record("login {'clientcode': 'A123', 'password': 'hunter2', 'totp': '654321'}"))
    assert "hunter2" not in line and "654321" not in line and "A123" not in line


def test_an_exception_is_logged_with_its_traceback():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = _record("failed")
        record.exc_info = sys.exc_info()
    entry = json.loads(observability.JsonFormatter().format(record))
    assert "ValueError: boom" in entry["exception"]


def test_the_format_is_a_setting():
    observability.configure_logging("INFO", "json")
    assert isinstance(logging.getLogger().handlers[0].formatter, observability.JsonFormatter)
    observability.configure_logging("INFO", "text")
    assert not isinstance(logging.getLogger().handlers[0].formatter,
                          observability.JsonFormatter)


# ── readiness and metrics ─────────────────────────────────────

def test_ready_means_usable_not_merely_running(client, monkeypatch):
    body = client.get("/api/ready")
    assert body.status_code == 200
    assert body.json()["ready"] is True
    assert body.json()["checks"] == {"database": True, "feed": True, "mode": "PAPER"}
    assert "series_by_source" in body.json()["feed"]

    class Down:
        async def __aenter__(self):
            raise RuntimeError("database is locked")

        async def __aexit__(self, *args):
            return False
    monkeypatch.setattr(observability, "session_scope", Down)
    broken = client.get("/api/ready")
    assert broken.status_code == 503
    assert broken.json()["checks"]["database"] is False


def test_live_mode_is_not_ready_without_a_broker_and_a_supervisor(client, monkeypatch):
    from app.broker.trading_mode import PAPER, trading_mode
    trading_mode.set_mode("LIVE", confirmed=True)
    try:
        body = client.get("/api/ready")
        assert body.status_code == 503
        assert body.json()["checks"]["broker_connected"] is False
        assert body.json()["checks"]["supervisor_running"] is False
    finally:
        trading_mode.set_mode(PAPER)


def test_metrics_are_prometheus_text_and_say_where_bars_came_from(client):
    client.get("/api/market/candles?symbol=EURUSD&timeframe=15m")
    body = client.get("/metrics")
    assert body.status_code == 200
    assert body.headers["content-type"].startswith("text/plain")
    text = body.text
    assert "# TYPE meridian_live_mode gauge" in text
    assert "meridian_live_mode 0.0" in text and "meridian_kill_switch 0.0" in text
    assert 'meridian_candle_series{source="synthetic"}' in text
    assert "meridian_database_up 1.0" in text
    assert text.count("# TYPE meridian_candle_series") == 1


def test_metrics_are_not_served_to_another_machine():
    with TestClient(app, client=REMOTE) as remote:
        assert remote.get("/metrics").status_code == 403
        assert remote.get("/api/ready").status_code == 403


def test_a_series_the_venue_did_not_supply_is_counted(monkeypatch):
    import time

    from app.data.store import store
    store.series("MCXGOLD", "1h").replace([])
    monkeypatch.setitem(observability.feed._source_failed, ("MCXGOLD", "1h"),  # noqa: SLF001
                        time.time())
    assert "MCXGOLD:1h" in observability.feed_freshness()["unsupplied_series"]
    # Once it has bars it is no longer missing, whatever happened earlier.
    store.series("MCXGOLD", "1h").replace([[1_700_000_000, 1, 1, 1, 1, 1]], source="angelone")
    try:
        assert "MCXGOLD:1h" not in observability.feed_freshness()["unsupplied_series"]
    finally:
        store.series("MCXGOLD", "1h").replace([])


# ── the lifecycle of a live execution ─────────────────────────

def test_the_ordinary_life_of_an_order_is_in_the_table():
    life = [S.VALIDATING, S.SUBMITTING, S.SUBMITTED, S.OPEN, S.PARTIALLY_FILLED,
            S.CANCEL_PENDING, S.FILLED, S.CLOSING, S.FILLED, S.CLOSING, S.CLOSED]
    assert all(order_states.allowed(a, b) for a, b in zip(life, life[1:]))
    assert order_states.allowed(S.SUBMITTING, S.UNKNOWN)
    assert order_states.allowed(S.UNKNOWN, S.REJECTED)
    assert order_states.allowed(S.REJECTED, S.SUBMITTING), "refused before it was sent: retry"
    assert order_states.allowed(S.OPEN, S.OPEN)
    assert set(order_states.TRANSITIONS) == set(S), "every state has an entry"


def test_what_is_finished_stays_finished():
    for final in (S.CLOSED, S.CANCELLED):
        for state in S:
            assert order_states.allowed(final, state) is (state == final)
    assert not order_states.allowed(S.FILLED, S.OPEN), "a fill is not un-filled"
    assert not order_states.allowed(S.REJECTED, S.FILLED)
    assert not order_states.allowed("NONSENSE", S.OPEN)


def _row(status):
    return LiveExecutionRow(recommendation_id="r", symbol="RELIANCE", side="BUY",
                            exchange="NSE", trading_symbol="RELIANCE-EQ",
                            instrument_token="2885", status=status)


def test_a_finished_execution_cannot_be_brought_back():
    row = _row("CLOSED")
    with pytest.raises(IllegalTransition, match="cannot become OPEN"):
        row.status = "OPEN"
    assert row.status == "CLOSED"
    with pytest.raises(IllegalTransition):
        _row("CANCELLED").status = "FILLED"


def test_any_other_surprise_is_reported_and_allowed():
    """The table has never met the real broker. A change it did not foresee
    must not stop a position from being supervised — but someone is told."""
    alerts.clear()
    row = _row("FILLED")
    row.status = "OPEN"
    assert row.status == "OPEN"
    alert = alerts.recent()[-1]
    assert (alert["level"], alert["code"]) == ("critical", "UNEXPECTED_TRANSITION")
    assert (alert["context"]["was"], alert["context"]["now"]) == ("FILLED", "OPEN")

    alerts.clear()
    row = _row("SUBMITTED")
    row.status = "FILLED"
    row.status = "CLOSED"
    assert alerts.recent() == []


def test_the_check_also_guards_rows_loaded_from_the_database():
    async def go():
        async with session_scope() as session:
            session.add(_row("CLOSED"))
        async with session_scope() as session:
            from sqlalchemy import select
            row = (await session.execute(select(LiveExecutionRow).where(
                LiveExecutionRow.recommendation_id == "r"))).scalars().first()
            try:
                row.status = "SUBMITTING"
            except IllegalTransition:
                return True
            finally:
                await session.delete(row)
        return False
    assert asyncio.run(go()) is True
