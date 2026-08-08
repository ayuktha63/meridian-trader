from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ManagementAction(str, Enum):
    """What to do with an open position, right now."""

    HOLD = "HOLD"                 # thesis intact, let the stop and target work
    TRAIL_STOP = "TRAIL_STOP"     # in profit — protect it by moving the stop up
    TAKE_PARTIAL = "TAKE_PARTIAL" # bank some of it, the rest can run
    EXIT = "EXIT"                 # the reason you entered is gone
    STOPPED = "STOPPED"           # price traded through the stop
    TARGET_HIT = "TARGET_HIT"     # price reached the target


class PositionCreate(BaseModel):
    symbol: str
    timeframe: str = "15m"
    side: PositionSide
    entry: float
    stop_loss: float
    take_profit: Optional[float] = None
    take_profit_2: Optional[float] = None
    lots: float = 1.0
    notes: Optional[str] = None
    analysis_id: Optional[str] = None


class PositionUpdate(BaseModel):
    stop_loss: Optional[float] = None
    #: Confirm the stop now resting at your broker. Setting this clears the
    #: "unmirrored" warning, so it must be an explicit act.
    broker_stop: Optional[float] = None
    take_profit: Optional[float] = None
    take_profit_2: Optional[float] = None
    lots: Optional[float] = None
    notes: Optional[str] = None


class Position(PositionCreate):
    id: str
    opened_at: float
    opened_bar_time: int = 0
    status: str = "open"          # open | closed
    closed_at: Optional[float] = None
    exit_price: Optional[float] = None
    realised_r: Optional[float] = None

    #: The stop as it was at entry — the risk unit. Never changes, because
    #: every R figure is measured against it. See `db.models.PositionRow`.
    initial_stop: float = 0.0
    protection_tier: int = 0
    peak_r: float = 0.0
    trough_r: float = 0.0
    #: The stop the trader has confirmed is actually resting at the broker.
    broker_stop: Optional[float] = None

    @property
    def risk_unit(self) -> float:
        """Price distance of 1R. Falls back to the live stop only for rows
        written before `initial_stop` existed."""
        return abs(self.entry - (self.initial_stop or self.stop_loss))


class LiveTick(BaseModel):
    """Per-tick state of an open position — the fast tier's output.

    Carries no analysis, only price arithmetic against levels the trader
    already set, which is why it can run every second.
    """

    position_id: str
    symbol: str
    price: float
    r_multiple: float = 0.0
    unrealised_pips: float = 0.0
    distance_to_stop_pips: float = 0.0
    distance_to_target_pips: float = 0.0
    alert: Optional[str] = None      # stop_hit | target_hit | approaching_stop | approaching_target | protect
    urgency: str = "normal"          # normal | elevated | immediate
    headline: str = ""
    detail: str = ""

    # ── protection ladder, evaluated on the same tick ────────
    protection_state: str = "at_risk"    # at_risk | armed | safe | locked
    protection_tier: int = 0
    locked_r: float = 0.0
    peak_r: float = 0.0
    trough_r: float = 0.0
    #: Where the stop should move to right now, when a rung has armed.
    suggested_stop: Optional[float] = None


class ManagementSignal(BaseModel):
    """Live guidance for an open position — the 'hold or sell' answer."""

    position_id: str
    action: ManagementAction = ManagementAction.HOLD
    urgency: str = "normal"       # normal | elevated | immediate
    headline: str = ""
    reasons: List[str] = Field(default_factory=list)
    cautions: List[str] = Field(default_factory=list)

    # Where the trade stands
    current_price: float = 0.0
    unrealised_pips: float = 0.0
    r_multiple: float = 0.0
    max_favourable_r: float = 0.0
    bars_held: int = 0
    distance_to_stop_pips: float = 0.0
    distance_to_target_pips: float = 0.0

    # Concrete adjustments, when there are any
    suggested_stop: Optional[float] = None
    suggested_stop_reason: Optional[str] = None
    reduce_fraction: Optional[float] = None

    #: What would change this answer.
    invalidation: str = ""
    thesis_intact: bool = True
