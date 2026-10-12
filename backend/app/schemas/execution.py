"""Live execution contracts — the recommendation-to-broker-order pipeline.

`ExecutionStatus` includes the full state machine from the live-trading spec
so later phases (partial fills, cancel flows) don't need a schema migration,
but this phase only ever produces a subset of it — see the docstring on
`AngelOneExecutionService.execute` for exactly which ones.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

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
    #: Estimated brokerage and statutory charges on the round trip, and what
    #: is left after them. The contract note is the record; this is the
    #: figure the daily loss limit is enforced on.
    charges: Optional[float] = None
    net_pnl: Optional[float] = None
    closed_at: Optional[float] = None

    #: Live mark-to-market for a still-open position — current price in,
    #: estimated round-trip charges out. None for anything not currently
    #: open, and None if a live price isn't available right now; never a
    #: stand-in for `net_pnl`, which is the realised figure once closed.
    last_price: Optional[float] = None
    unrealised_pnl: Optional[float] = None

    created_at: float
    updated_at: float


class LiveTradingOverview(BaseModel):
    """Everything the live-trading screen needs in one call: the real
    wallet, today's ledger (the same one the risk gate enforces against),
    and the executions split into what's open versus what's history."""

    balance: float
    balance_source: str
    currency: str

    open_risk: float
    realised_today: float
    trades_today: int

    open_positions: List[ExecutionRecord]
    history: List[ExecutionRecord]


class ExecutionPreview(BaseModel):
    """The order `execute` would send right now, and whether the gate would
    let it. Shown in the confirmation dialog: what you confirm is what goes."""

    allowed: bool
    check: Optional[str] = None
    reason: Optional[str] = None

    symbol: str
    side: str
    trading_symbol: Optional[str] = None
    exchange: Optional[str] = None
    quantity: Optional[float] = None
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    order_value: Optional[float] = None
    #: What a stop-out costs, and the budget it was sized inside.
    risk: Optional[float] = None
    risk_budget: Optional[float] = None
    #: Estimated brokerage and statutory charges on top of `risk` if stopped.
    charges: Optional[float] = None
    margin_required: Optional[float] = None
    available_funds: Optional[float] = None
    last_price: Optional[float] = None
    edge: Optional[str] = None


class ExecuteRequest(BaseModel):
    """What the frontend actually gets to control: nothing about the trade
    itself. Only the explicit go-ahead — every price/quantity/instrument
    field is derived server-side from the stored recommendation."""
    confirmed: bool = False
