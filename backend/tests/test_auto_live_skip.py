"""Auto-live stops asking about instruments the gate always refuses."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app import alerts
from app.broker.risk_gate import blocked_for_good
from app.routers import ws
from app.schemas.analysis import Decision


def test_commodity_and_currency_segments_are_blocked_locally_and_stocks_are_not():
    assert "MCX" in blocked_for_good("MCXSILVER")
    assert "lots" in blocked_for_good("MCXGOLD")
    assert "CDS" in blocked_for_good("USDINR")
    assert blocked_for_good("RELIANCE") is None
    assert blocked_for_good("NIFTY") is None
    assert blocked_for_good("NOPE") == "Unknown instrument."


def test_auto_live_does_not_ask_about_mcx_and_says_so_once(monkeypatch):
    asked = []

    async def execute(*args, **kwargs):
        asked.append(args)
    monkeypatch.setattr(ws.angel_execution, "execute", execute)
    monkeypatch.setattr(ws.trading_mode, "can_auto_trade_live", lambda: True)
    alerts.clear()
    result = SimpleNamespace(symbol="MCXSILVER", timeframe="15m", id="a1",
                             verdict=SimpleNamespace(decision=Decision.SELL))
    for _ in range(5):
        asyncio.run(ws._auto_trade_live_entry(SimpleNamespace(), result))  # noqa: SLF001
    assert asked == [], "the gate is never asked, so no refusal row is written"
    notices = [a for a in alerts.recent(0) if a["code"] == "AUTO_LIVE_SKIPPED"]
    assert len(notices) == 1 and "MCXSILVER" in notices[0]["message"]
