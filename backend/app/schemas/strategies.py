from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Condition(BaseModel):
    """One testable precondition of a strategy, and whether it currently holds.

    Reporting the failed conditions matters as much as the passed ones — a
    strategy that is 4-of-6 tells you what you are waiting for.
    """

    name: str
    met: bool
    detail: str = ""
    weight: float = 1.0


class ScenarioPoint(BaseModel):
    time: int
    price: float


class StrategyResult(BaseModel):
    key: str
    name: str
    family: str                       # trend | reversion | breakout | smc | momentum
    description: str = ""
    stance: str = "flat"              # long | short | flat
    conviction: float = 0.0           # 0-100, how well its own conditions are met
    conditions: List[Condition] = Field(default_factory=list)
    conditions_met: int = 0
    conditions_total: int = 0

    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    take_profit_2: Optional[float] = None
    risk_reward: Optional[float] = None
    horizon_bars: int = 0

    rationale: List[str] = Field(default_factory=list)
    waiting_for: List[str] = Field(default_factory=list)
    note: str = ""

    #: The scenario this strategy implies if its thesis plays out. Not a
    #: forecast — the path price would take *given* the thesis is correct.
    projection: List[ScenarioPoint] = Field(default_factory=list)
    #: The path to invalidation, drawn so the downside is as visible as the up.
    invalidation_path: List[ScenarioPoint] = Field(default_factory=list)


class StrategyReport(BaseModel):
    results: List[StrategyResult] = Field(default_factory=list)
    consensus_stance: str = "flat"
    consensus_score: float = 0.0      # -1..+1
    agreement: float = 0.0            # 0..1 across strategies that took a side
    leader: Optional[str] = None
    summary: str = ""
    disclaimer: str = (
        "These are scenarios, not forecasts. Each path shows where price would "
        "go if that strategy's thesis is correct — it carries no claim that it "
        "will be."
    )
    by_family: Dict[str, str] = Field(default_factory=dict)
