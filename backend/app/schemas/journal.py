from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class JournalCreate(BaseModel):
    symbol: str
    timeframe: str
    decision: str
    confidence: float = 0.0
    probability: float = 0.0
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: Optional[float] = None
    summary: str = ""
    reasons: Dict[str, Any] = Field(default_factory=dict)
    indicators: Dict[str, Any] = Field(default_factory=dict)
    structure: Dict[str, Any] = Field(default_factory=dict)
    claude_response: Dict[str, Any] = Field(default_factory=dict)
    snapshot: Optional[str] = None          # data-url PNG of the chart
    analysis_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class JournalOutcome(BaseModel):
    outcome: str                             # win | loss | breakeven | cancelled | open
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    r_multiple: Optional[float] = None
    notes: Optional[str] = None


class JournalEntry(JournalCreate):
    id: str
    created_at: float
    outcome: str = "open"
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    r_multiple: Optional[float] = None
    closed_at: Optional[float] = None


class JournalStats(BaseModel):
    total: int = 0
    open: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    total_r: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    best_r: float = 0.0
    worst_r: float = 0.0
    by_symbol: Dict[str, Any] = Field(default_factory=dict)
    by_decision: Dict[str, Any] = Field(default_factory=dict)
    accuracy_by_confidence: Dict[str, Any] = Field(default_factory=dict)


class BacktestRequest(BaseModel):
    symbol: str
    timeframe: str = "1h"
    bars: int = 1500
    #: "engine" is the live verdict — what the trade panel shows and Execute
    #: Live sends. The others are the named strategies from the Plays tab
    #: (trend_continuation, mean_reversion, breakout, smart_money, momentum),
    #: and "random" is the coin-flip control.
    strategy: str = "engine"
    #: Take only setups at or above this confidence (engine) or conviction
    #: (a named strategy)…
    min_confidence: float = 55.0
    #: …and with at least this reward:risk in their own plan. The stop and
    #: target are the strategy's, not derived from this number.
    risk_reward: float = 2.0
    #: Stop distance in ATRs — the random control only.
    atr_stop_mult: float = 1.5
    #: Ratchet the stop up the protection ladder, as live does when
    #: auto-protect is on.
    use_ladder: bool = True
    use_ai: bool = False               # accepted and ignored: replay never calls Claude
    starting_balance: float = 10000.0
    risk_pct: float = 1.0
    #: Charge spread, brokerage, statutory charges and stop slippage. Off only
    #: to see how much of a result the costs were.
    include_costs: bool = True
    #: Which venue's charges (`costs.PROFILES`). None is the instrument's own.
    cost_profile: Optional[str] = None
    #: Share of the bars, at the end, held out as the out-of-sample segment.
    oos_fraction: float = 0.3
    seed: int = 7


class BacktestTrade(BaseModel):
    index: int
    time: int
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    exit_time: Optional[int] = None
    exit_price: Optional[float] = None
    outcome: str = "open"
    r_multiple: float = 0.0
    pnl: float = 0.0
    bars_held: int = 0
    confidence: float = 0.0
    reason: str = ""
    #: What the round trip cost, in R. `r_multiple` is already net of it.
    cost_r: float = 0.0
    #: stop | target | eod (intraday square-off) | timeout
    exit_reason: str = ""


class EdgeVerdict(BaseModel):
    """Is there a measured edge after costs, on data the parameters never saw?"""

    passed: bool = False
    reason: str = "Not evaluated."
    trades: int = 0
    expectancy: float = 0.0
    #: 90% bootstrap interval on per-trade expectancy, in R.
    ci_low: float = 0.0
    ci_high: float = 0.0
    #: Share of bootstrap resamples with no edge. What the live gate corrects
    #: for the number of markets tested.
    p_value: float = 1.0
    method: str = "holdout"       # holdout | walk_forward


class BacktestMetrics(BaseModel):
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    average_r: float = 0.0
    total_r: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    expectancy: float = 0.0
    largest_win_r: float = 0.0
    largest_loss_r: float = 0.0
    longest_win_streak: int = 0
    longest_loss_streak: int = 0
    ending_balance: float = 0.0
    return_pct: float = 0.0
    exposure_pct: float = 0.0


class BacktestResult(BaseModel):
    symbol: str
    timeframe: str
    strategy: str
    bars_tested: int
    from_time: int
    to_time: int
    metrics: BacktestMetrics
    trades: List[BacktestTrade] = Field(default_factory=list)
    equity_curve: List[Dict[str, float]] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    #: The same run split in time: fitted segment vs the part held out.
    in_sample: Optional[BacktestMetrics] = None
    out_of_sample: Optional[BacktestMetrics] = None
    edge: EdgeVerdict = Field(default_factory=EdgeVerdict)
    #: Walk-forward only: the parameters chosen for each fold…
    folds: List[Dict[str, float]] = Field(default_factory=list)
    #: …and the ones fitted on the most recent stretch, to trade next. A live
    #: order must clear these to be the kind of trade the test measured.
    next_parameters: Dict[str, float] = Field(default_factory=dict)
    #: Where the bars came from: a venue's name, or "synthetic" for the
    #: simulator — whose results are never evidence about a market.
    data_source: str = "unknown"
