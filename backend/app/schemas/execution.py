"""Live execution contracts — the recommendation-to-broker-order pipeline.

`ExecutionStatus` includes the full state machine from the live-trading spec
so later phases (partial fills, cancel flows) don't need a schema migration,
but this phase only ever produces a subset of it — see the docstring on
`AngelOneExecutionService.execute` for exactly which ones.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class ExecutionStatus(str, Enum):
    RECOMMENDED = "RECOMMENDED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    VALIDATING = "VALIDATING"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


class RiskGateResult(BaseModel):
    allowed: bool
    reason: Optional[str] = None
    #: Which named check produced the result — lets the UI and the tests
    #: assert on *why*, not just pass/fail.
    check: Optional[str] = None
    details: dict = {}


class ExecutionRecord(BaseModel):
    id: str
    recommendation_id: str
    broker: str = "ANGEL_ONE"

    symbol: str
    side: str
    exchange: str
    trading_symbol: str
    instrument_token: str

    requested_quantity: float
    filled_quantity: float = 0.0
    requested_price: Optional[float] = None
    average_fill_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    risk_amount: Optional[float] = None

    status: ExecutionStatus
    broker_order_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    protection_status: str = "NONE"
    stop_order_id: Optional[str] = None
    target_order_id: Optional[str] = None
    protection_tier: int = 0
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross_pnl: Optional[float] = None
    closed_at: Optional[float] = None

    created_at: float
    updated_at: float


class ExecuteRequest(BaseModel):
    """What the frontend actually gets to control: nothing about the trade
    itself. Only the explicit go-ahead — every price/quantity/instrument
    field is derived server-side from the stored recommendation."""
    confirmed: bool = False
