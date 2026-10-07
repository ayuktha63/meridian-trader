"""Angel One / live-trading API contracts.

Nothing here ever carries a credential — status responses report *whether*
Angel One is configured and connected, never the key, password or TOTP
secret used to get there.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class AngelStatus(BaseModel):
    configured: bool
    connected: bool
    session_age_seconds: Optional[float] = None
    last_error: Optional[str] = None


class TradingModeStatus(BaseModel):
    mode: str
    kill_switch_active: bool
    #: Why it tripped — "Activated manually.", or the limit that tripped it.
    kill_switch_reason: Optional[str] = None
    kill_switch_activated_at: Optional[float] = None
    live_confirmed_at: Optional[float] = None


class TradingModeRequest(BaseModel):
    mode: str
    confirmed: bool = False


class InstrumentCapability(BaseModel):
    supported: bool
    reason: Optional[str] = None
    exchange: Optional[str] = None
    trading_symbol: Optional[str] = None
    lot_size: Optional[int] = None
    tick_size: Optional[float] = None


class AngelAccount(BaseModel):
    profile: Dict[str, Any]
    funds: Dict[str, Any]
    positions: List[Dict[str, Any]]
    orders: List[Dict[str, Any]]
