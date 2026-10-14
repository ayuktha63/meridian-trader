from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ProtectionState(str, Enum):
    """How much of the trade is still exposed."""

    AT_RISK = "at_risk"        # a stop-out costs the full 1R
    ARMED = "armed"            # a tier has triggered but the stop has not moved yet
    SAFE = "safe"              # stop at or beyond breakeven — a stop-out costs nothing
    LOCKED = "locked"          # stop beyond breakeven — a stop-out books a profit


class ProtectionTier(BaseModel):
    """One rung of the ladder: when it arms, and where the stop goes."""

    index: int
    name: str
    trigger_r: float           # profit in R that arms this tier
    locked_r: float            # R secured once the stop moves (negative = still risking)
    stop: float                # the price the stop moves to
    reached: bool = False
    active: bool = False       # the tier currently in force
    note: str = ""


class ProtectionPlan(BaseModel):
    """The whole risk-management picture for one open position."""

    position_id: str
    state: ProtectionState = ProtectionState.AT_RISK
    headline: str = ""
    detail: str = ""

    initial_stop: float = 0.0
    current_stop: float = 0.0
    #: Where the stop should be right now given how far the trade has run.
    suggested_stop: Optional[float] = None
    suggested_reason: str = ""
    #: True when `suggested_stop` is tighter than `current_stop` — i.e. there
    #: is protection available that has not been taken.
    action_available: bool = False

    r_multiple: float = 0.0
    peak_r: float = 0.0
    #: Worst point the trade has been through. A trade that ran to -0.9R before
    #: working was nearly a loss, and the journal should remember that.
    trough_r: float = 0.0
    #: How much of the peak has been handed back, as a fraction.
    giveback: float = 0.0

    #: R that is secured if the stop is hit right now. Negative until the
    #: breakeven tier arms.
    locked_r: float = 0.0
    #: Cash exposed if stopped now, in the account currency.
    risk_amount: float = 0.0
    #: Cash secured if stopped now (negative while still at risk).
    secured_amount: float = 0.0

    tiers: List[ProtectionTier] = Field(default_factory=list)
    next_tier: Optional[ProtectionTier] = None
    #: Distance in price and in pips to arming the next rung.
    to_next_tier_pips: Optional[float] = None

    #: Set when the tracked stop and the broker stop disagree. The single most
    #: dangerous state: the screen says protected, the broker says otherwise.
    broker_stop: Optional[float] = None
    broker_synced: bool = True
    broker_warning: str = ""

    notes: List[str] = Field(default_factory=list)


class GuardrailStatus(str, Enum):
    CLEAR = "clear"
    CAUTION = "caution"
    HALT = "halt"


class Guardrail(BaseModel):
    key: str
    label: str
    status: GuardrailStatus = GuardrailStatus.CLEAR
    value: float = 0.0
    limit: float = 0.0
    unit: str = ""             # currency | R | count
    detail: str = ""


class AccountRisk(BaseModel):
    """Account-level circuit breaker.

    Per-trade risk management stops one trade from being a disaster. This stops
    a *sequence* of them from being one, which is how accounts actually die.
    """

    status: GuardrailStatus = GuardrailStatus.CLEAR
    headline: str = ""
    reason: str = ""
    can_open_new: bool = True

    balance: float = 0.0
    #: "angel_one" once LIVE and connected and the wallet call succeeded;
    #: "settings" the rest of the time (DEMO, or LIVE with no live read yet).
    balance_source: str = "settings"
    currency: str = "USD"
    risk_per_trade_pct: float = 1.0

    realised_today_r: float = 0.0
    realised_today_amount: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0

    open_positions: int = 0
    open_risk_r: float = 0.0
    open_risk_amount: float = 0.0

    guardrails: List[Guardrail] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
