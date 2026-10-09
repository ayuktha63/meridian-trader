"""Angel One connection, account state, and the PAPER/LIVE mode switch.

Order execution (pre-trade risk gate, instrument resolution, submission) is
wired in — see `routers/execution.py` and `broker/risk_gate.py`. This router
only ever reports broker state or flips the mode flag; it never places an
order itself, on purpose, so a bug here can never place a trade.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from .. import alerts
from ..broker.angelone import angel_auth, angel_broker, angel_instruments
from ..broker.angelone.reconciler import reconciler
from ..broker.angelone.auth import AngelOneAuthError
from ..broker.angelone.client import AngelOneOrderError
from ..broker.trading_mode import LIVE, PAPER, trading_mode
from ..data.feed import feed
from ..schemas.broker import (
    AngelAccount, AngelStatus, FlattenRequest, InstrumentCapability, TradingModeRequest,
    TradingModeStatus,
)

router = APIRouter(tags=["broker"])

NOT_CONNECTED = "Not connected to Angel One."


# ── angel one connection ─────────────────────────────────────

@router.get("/api/broker/angel/status", response_model=AngelStatus)
async def angel_status() -> AngelStatus:
    return AngelStatus(**angel_auth.status())


@router.post("/api/broker/angel/connect", response_model=AngelStatus)
async def angel_connect() -> AngelStatus:
    try:
        status = await angel_broker.connect()
    except AngelOneAuthError as exc:
        raise HTTPException(502, str(exc)) from exc
    # Angel One becomes available as a market-data source the instant it's
    # connected — reload the feed so NSE/MCX/CDS symbols switch from
    # synthetic to real prices without needing a backend restart.
    asyncio.create_task(feed.reload("angel one connected"))
    return AngelStatus(**status)


@router.post("/api/broker/angel/disconnect", response_model=AngelStatus)
async def angel_disconnect() -> AngelStatus:
    await angel_broker.disconnect()
    asyncio.create_task(feed.reload("angel one disconnected"))
    return AngelStatus(**angel_auth.status())


@router.get("/api/broker/angel/account", response_model=AngelAccount)
async def angel_account() -> AngelAccount:
    if not angel_auth.connected:
        raise HTTPException(409, NOT_CONNECTED)
    try:
        profile = await angel_broker.get_profile()
        funds = await angel_broker.get_funds()
        positions = await angel_broker.get_positions()
        orders = await angel_broker.get_orders()
    except AngelOneOrderError as exc:
        raise HTTPException(502, str(exc)) from exc
    return AngelAccount(profile=profile, funds=funds or {},
                        positions=positions, orders=orders)


@router.get("/api/broker/angel/positions")
async def angel_positions() -> list:
    if not angel_auth.connected:
        raise HTTPException(409, NOT_CONNECTED)
    try:
        return await angel_broker.get_positions()
    except AngelOneOrderError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/api/broker/angel/orders")
async def angel_orders() -> list:
    if not angel_auth.connected:
        raise HTTPException(409, NOT_CONNECTED)
    try:
        return await angel_broker.get_orders()
    except AngelOneOrderError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/api/broker/angel/instrument/{symbol}/capability",
           response_model=InstrumentCapability)
async def angel_instrument_capability(symbol: str) -> InstrumentCapability:
    return InstrumentCapability(**await angel_instruments.capability(symbol.upper()))


# ── trading mode ──────────────────────────────────────────────

@router.get("/api/trading/mode", response_model=TradingModeStatus)
async def get_trading_mode() -> TradingModeStatus:
    return TradingModeStatus(**trading_mode.status())


@router.post("/api/trading/mode", response_model=TradingModeStatus)
async def set_trading_mode(payload: TradingModeRequest) -> TradingModeStatus:
    mode = payload.mode.upper()
    if mode not in (PAPER, LIVE):
        raise HTTPException(422, "mode must be PAPER or LIVE")
    if mode == LIVE and not angel_auth.connected:
        raise HTTPException(
            409, "Cannot switch to LIVE — Angel One is not connected. "
                "Connect first, then switch modes.")
    try:
        trading_mode.set_mode(mode, confirmed=payload.confirmed)
    except PermissionError as exc:
        raise HTTPException(428, str(exc)) from exc
    return TradingModeStatus(**trading_mode.status())


@router.post("/api/trading/kill-switch", response_model=TradingModeStatus)
async def set_kill_switch(activate: bool = True) -> TradingModeStatus:
    """Stop: no new live orders, and every entry still working is pulled.
    Protective stops stay where they are; closing positions is the separate,
    explicit `flatten-all`."""
    if activate:
        trading_mode.activate_kill_switch("Activated manually.")
        try:
            cancelled = await reconciler.cancel_working_entries()
        except Exception as exc:  # noqa: BLE001 - the switch itself has tripped
            alerts.emit("critical", "KILL_SWITCH_CANCEL_FAILED",
                        "Kill switch is on, but working orders could not be "
                        "cancelled — check Angel One.", error=str(exc))
        else:
            if cancelled:
                alerts.emit("warning", "KILL_SWITCH",
                            "Kill switch: {} working order(s) cancelled.".format(cancelled))
    else:
        trading_mode.deactivate_kill_switch()
    return TradingModeStatus(**trading_mode.status())


@router.post("/api/trading/flatten-all")
async def flatten_all(payload: FlattenRequest) -> dict:
    """Close every live position at market. Trips the kill switch first."""
    if not payload.confirmed:
        raise HTTPException(428, "Flattening every position requires confirmed=true.")
    trading_mode.activate_kill_switch("Flatten-all requested.")
    try:
        await reconciler.cancel_working_entries()
        closing = await reconciler.flatten_all()
    except AngelOneOrderError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"closing": closing, **trading_mode.status()}


@router.get("/api/alerts")
async def list_alerts(since: float = 0.0) -> list:
    return alerts.recent(since)
