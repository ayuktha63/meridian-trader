"""Angel One connection, account state, and the PAPER/LIVE mode switch.

This router only ever reports broker state or flips the mode flag — it does
not place orders. Order execution is a separate, larger piece (pre-trade risk
gate, instrument resolution, broker-side monitoring) that isn't wired in yet;
see TRADING.md / the live-trading spec for the remaining phases. Exposing
connect/status/mode now, honestly, is more useful than a fake "Execute Live"
button that doesn't actually check anything.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..broker.angelone import angel_auth, angel_broker, angel_instruments
from ..broker.angelone.auth import AngelOneAuthError
from ..broker.angelone.client import AngelOneOrderError
from ..broker.trading_mode import LIVE, PAPER, trading_mode
from ..schemas.broker import (
    AngelAccount, AngelStatus, InstrumentCapability, TradingModeRequest,
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
    return AngelStatus(**status)


@router.post("/api/broker/angel/disconnect", response_model=AngelStatus)
async def angel_disconnect() -> AngelStatus:
    await angel_broker.disconnect()
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
    if activate:
        trading_mode.activate_kill_switch()
    else:
        trading_mode.deactivate_kill_switch()
    return TradingModeStatus(**trading_mode.status())
