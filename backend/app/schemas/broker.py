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
    #: Logins that have failed in a row, and how long until the next
    #: automatic one — None when one may be made now.
    failed_logins: int = 0
    login_retry_in_seconds: Optional[float] = None
    #: Angel One's own available-cash figure, set only once trading is LIVE,
    #: the session is connected, and the wallet read actually succeeded —
    #: never a stand-in for it, so the UI can tell "your money" from "not yet known".
    available_funds: Optional[float] = None


class TradingModeStatus(BaseModel):
    mode: str
    kill_switch_active: bool
    #: Why it tripped — "Activated manually.", or the limit that tripped it.
    kill_switch_reason: Optional[str] = None
    kill_switch_activated_at: Optional[float] = None
    live_confirmed_at: Optional[float] = None
    #: Unattended execution armed. Always false after a restart, a switch
    #: away from LIVE, or a kill-switch trip — see TradingModeStore.
    auto_live_enabled: bool = False


class TradingModeRequest(BaseModel):
    mode: str
    confirmed: bool = False


class AutoLiveRequest(BaseModel):
    enabled: bool
    confirmed: bool = False


class FlattenRequest(BaseModel):
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
