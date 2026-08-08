from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ExpiryChoice(BaseModel):
    date: str
    kind: str                 # weekly | monthly | next-month
    days_to_expiry: int
    note: str = ""


class OptionLeg(BaseModel):
    contract: str = ""
    strike: float
    option_type: str          # CE | PE
    moneyness: str            # ITM | ATM | OTM
    expiry: str
    days_to_expiry: int

    estimated_premium: float
    delta: float
    theta_per_day: float
    theta_pct_per_day: float = 0.0
    vega: float

    cost_per_lot: float
    max_loss_per_lot: float
    #: The underlying stop translated into premium through delta — the number
    #: you actually place in the broker.
    premium_stop: float
    premium_target: Optional[float] = None
    breakeven: float
    suggested_lots: Optional[int] = None
    note: str = ""


class DerivativeSuggestion(BaseModel):
    """How to express a directional call as a listed contract."""

    symbol: str
    underlying_price: float
    direction: str            # long | short
    lot_size: float
    implied_vol_estimate: float = 0.0     # annualised %, from realised vol

    expiries: List[ExpiryChoice] = Field(default_factory=list)

    futures_symbol: Optional[str] = None
    futures_expiry: Optional[str] = None
    futures_risk_per_lot: float = 0.0
    futures_reward_per_lot: Optional[float] = None
    futures_lots: Optional[int] = None
    futures_notes: List[str] = Field(default_factory=list)

    #: Where the levels came from — "verdict" when the engine issued a BUY or
    #: SELL, otherwise the name of the strategy whose setup is being priced.
    source: str = "verdict"
    options: List[OptionLeg] = Field(default_factory=list)
    recommended: Optional[str] = None
    notes: List[str] = Field(default_factory=list)
    disclaimer: str = ""
