from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class PaperStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class ExitReason(str, Enum):
    STOP = "stop"                 # price traded through the stop
    TARGET = "target"             # price reached the target
    SIGNAL = "signal"             # the engine turned against the position
    GUARDRAIL = "guardrail"       # an account limit halted trading
    MANUAL = "manual"             # closed from the UI
    STALE = "stale"               # went nowhere for too long


class PaperTrade(BaseModel):
    id: str
    account_id: str
    symbol: str
    timeframe: str
    side: str                     # LONG | SHORT
    status: PaperStatus = PaperStatus.OPEN

    #: Fills are recorded at the price actually crossed, never the mid.
    entry: float
    entry_at: float
    exit: Optional[float] = None
    exit_at: Optional[float] = None
    exit_reason: Optional[ExitReason] = None

    stop_loss: float
    initial_stop: float
    take_profit: Optional[float] = None
    quantity: float = 0.0         # units of the instrument
    notional: float = 0.0         # quantity x entry, in account currency

    #: Costs are subtracted from P&L, never hidden. A paper run that ignores
    #: them is the single easiest way to convince yourself of an edge you do
    #: not have.
    entry_cost: float = 0.0
    exit_cost: float = 0.0

    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    realised_r: Optional[float] = None
    peak_r: float = 0.0
    trough_r: float = 0.0
    protection_tier: int = 0

    confidence: float = 0.0
    decision_source: str = ""     # claude | rule_engine
    reason: str = ""
    analysis_id: Optional[str] = None


class PaperAccount(BaseModel):
    id: str
    currency: str = "USD"
    starting_balance: float = 10_000.0
    #: Cash. Only changes when a trade closes.
    balance: float = 10_000.0
    #: Balance plus the open position's unrealised P&L, marked to live price.
    equity: float = 10_000.0

    enabled: bool = False
    risk_per_trade_pct: float = 1.0
    min_confidence: float = 55.0
    #: Symbols the auto-trader is allowed to take. Empty means "the one you
    #: are watching", which keeps a demo run comprehensible.
    symbols: List[str] = Field(default_factory=list)
    #: Timeframes the auto-trader is allowed to take. Empty means "any".
    timeframes: List[str] = Field(default_factory=list)

    open_trades: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0

    # ── performance, computed from closed trades ──────────────
    realised_pnl: float = 0.0
    unrealised_pnl: float = 0.0
    total_costs: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    max_drawdown: float = 0.0
    peak_equity: float = 0.0
    return_pct: float = 0.0

    #: Why the auto-trader is or is not doing anything right now.
    status_line: str = ""
    blocked_reason: str = ""


class PaperState(BaseModel):
    """Everything the home screen needs in one payload."""

    account: PaperAccount
    open_trade: Optional[PaperTrade] = None
    recent: List[PaperTrade] = Field(default_factory=list)
