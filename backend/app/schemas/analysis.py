from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .drawings import DrawingAnalytics
from .positions import ManagementSignal
from .risk import AccountRisk, ProtectionPlan
from .derivatives import DerivativeSuggestion
from .strategies import StrategyReport


class Decision(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"


class Bias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


# ── indicators ────────────────────────────────────────────────

class IndicatorSnapshot(BaseModel):
    """Latest value of every indicator plus derived interpretation."""

    ema: Dict[str, float] = Field(default_factory=dict)          # {"20": 1.0851, ...}
    sma: Dict[str, float] = Field(default_factory=dict)
    vwap: Optional[float] = None
    vwap_bands: Dict[str, float] = Field(default_factory=dict)
    atr: Optional[float] = None
    atr_pips: Optional[float] = None
    atr_pct: Optional[float] = None
    rsi: Optional[float] = None
    rsi_divergence: Optional[str] = None
    macd: Dict[str, float] = Field(default_factory=dict)         # line/signal/hist
    macd_state: Optional[str] = None
    adx: Optional[float] = None
    di_plus: Optional[float] = None
    di_minus: Optional[float] = None
    stoch_rsi: Dict[str, float] = Field(default_factory=dict)    # k/d
    ichimoku: Dict[str, float] = Field(default_factory=dict)
    ichimoku_state: Optional[str] = None
    supertrend: Optional[float] = None
    supertrend_dir: Optional[int] = None
    bollinger: Dict[str, float] = Field(default_factory=dict)    # upper/mid/lower/width/pctb
    bb_squeeze: bool = False
    obv: Optional[float] = None
    obv_slope: Optional[float] = None
    mfi: Optional[float] = None
    cci: Optional[float] = None
    roc: Optional[float] = None
    williams_r: Optional[float] = None
    pivots: Dict[str, float] = Field(default_factory=dict)
    volume_profile: Dict[str, Any] = Field(default_factory=dict)
    relative_volume: Optional[float] = None
    volatility_regime: str = "normal"          # compressed | normal | expanded
    trend_strength: str = "weak"               # weak | moderate | strong


# ── market structure ──────────────────────────────────────────

class SwingPoint(BaseModel):
    index: int
    time: int
    price: float
    kind: str            # swing_high | swing_low
    label: Optional[str] = None      # HH | HL | LH | LL
    strength: int = 1


class StructureEvent(BaseModel):
    kind: str            # BOS | CHoCH | liquidity_sweep | equal_highs | equal_lows
    direction: str       # bullish | bearish
    time: int
    index: int
    price: float
    reference_price: Optional[float] = None
    bars_ago: int = 0
    note: Optional[str] = None
    significance: float = 0.5


class Zone(BaseModel):
    kind: str            # order_block | breaker | mitigation | fvg | supply | demand | liquidity_pool
    direction: str       # bullish | bearish
    top: float
    bottom: float
    start_time: int
    end_time: int
    start_index: int
    end_index: int
    mitigated: bool = False
    tested: int = 0
    strength: float = 0.5
    distance_pct: float = 0.0
    note: Optional[str] = None

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0


class MarketStructure(BaseModel):
    trend: Bias = Bias.NEUTRAL
    structure_label: str = "ranging"       # uptrend | downtrend | ranging | transition
    swings: List[SwingPoint] = Field(default_factory=list)
    events: List[StructureEvent] = Field(default_factory=list)
    order_blocks: List[Zone] = Field(default_factory=list)
    breaker_blocks: List[Zone] = Field(default_factory=list)
    mitigation_blocks: List[Zone] = Field(default_factory=list)
    fair_value_gaps: List[Zone] = Field(default_factory=list)
    supply_zones: List[Zone] = Field(default_factory=list)
    demand_zones: List[Zone] = Field(default_factory=list)
    liquidity_pools: List[Zone] = Field(default_factory=list)
    equal_highs: List[float] = Field(default_factory=list)
    equal_lows: List[float] = Field(default_factory=list)
    range_high: Optional[float] = None
    range_low: Optional[float] = None
    equilibrium: Optional[float] = None
    premium_discount: str = "equilibrium"   # premium | discount | equilibrium
    last_bos: Optional[StructureEvent] = None
    last_choch: Optional[StructureEvent] = None
    support_levels: List[float] = Field(default_factory=list)
    resistance_levels: List[float] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


# ── patterns ──────────────────────────────────────────────────

class PatternHit(BaseModel):
    name: str
    category: str        # chart | candlestick | harmonic
    direction: str       # bullish | bearish | neutral
    start_index: int
    end_index: int
    start_time: int
    end_time: int
    confidence: float = 0.5
    target: Optional[float] = None
    invalidation: Optional[float] = None
    points: List[Dict[str, float]] = Field(default_factory=list)
    note: Optional[str] = None


# ── multi timeframe ───────────────────────────────────────────

class TimeframeView(BaseModel):
    timeframe: str
    bias: Bias = Bias.NEUTRAL
    structure: str = "ranging"
    score: float = 0.0             # -1 .. +1
    rsi: Optional[float] = None
    adx: Optional[float] = None
    ema_stack: Optional[str] = None
    close: Optional[float] = None
    key_level: Optional[float] = None
    note: Optional[str] = None


class MTFReport(BaseModel):
    views: List[TimeframeView] = Field(default_factory=list)
    aligned_bias: Bias = Bias.NEUTRAL
    alignment_score: float = 0.0    # 0..1 agreement strength
    weighted_score: float = 0.0     # -1..+1
    conflicts: List[str] = Field(default_factory=list)
    narrative: str = ""


# ── confidence ────────────────────────────────────────────────

class ConfidenceComponent(BaseModel):
    name: str
    weight: float
    raw_score: float       # -1 .. +1 directional
    contribution: float    # weighted points toward final confidence
    detail: str = ""


class ConfidenceBreakdown(BaseModel):
    components: List[ConfidenceComponent] = Field(default_factory=list)
    directional_score: float = 0.0     # -1 .. +1
    agreement: float = 0.0             # 0..1, how much components agree
    final_confidence: float = 0.0      # 0..100
    conflict: bool = False
    reason: str = ""


# ── ai verdict ────────────────────────────────────────────────

class AIVerdict(BaseModel):
    """The strict JSON contract Claude must satisfy."""

    decision: Decision = Decision.WAIT
    confidence: float = 0.0
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    take_profit_2: Optional[float] = None
    risk_reward: Optional[float] = None
    trend: str = "neutral"
    market_structure: str = "ranging"
    probability: float = 0.0
    bullish_points: List[str] = Field(default_factory=list)
    bearish_points: List[str] = Field(default_factory=list)
    invalidation: Optional[str] = None
    timeframe_alignment: Optional[str] = None
    key_levels: List[float] = Field(default_factory=list)
    summary: str = ""
    uncertainty: str = ""
    source: str = "claude"             # claude | rule_engine
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    raw: Optional[str] = None


# ── overlays ──────────────────────────────────────────────────

class OverlayObject(BaseModel):
    """A primitive the frontend renders on the Konva AI layer."""

    id: str
    kind: str            # line | ray | box | zone | label | arrow | marker | path
    role: str            # entry | stop | target | order_block | fvg | liquidity | trendline | projection | support | resistance | profit_zone | risk_zone
    color: str
    points: List[Dict[str, float]] = Field(default_factory=list)   # [{time, price}]
    price: Optional[float] = None
    top: Optional[float] = None
    bottom: Optional[float] = None
    start_time: Optional[int] = None
    end_time: Optional[int] = None
    label: Optional[str] = None
    dash: Optional[List[int]] = None
    width: int = 1
    opacity: float = 1.0
    fill: Optional[str] = None


# ── trade plan ────────────────────────────────────────────────

class TradePlan(BaseModel):
    decision: Decision = Decision.WAIT
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    take_profit_2: Optional[float] = None
    risk_pips: Optional[float] = None
    reward_pips: Optional[float] = None
    risk_reward: Optional[float] = None
    expected_move_pips: Optional[float] = None
    atr_pips: Optional[float] = None
    spread_pips: Optional[float] = None
    suggested_lots: Optional[float] = None
    risk_amount: Optional[float] = None
    account_balance: Optional[float] = None
    position_notes: List[str] = Field(default_factory=list)


# ── full analysis payload ─────────────────────────────────────

class AnalysisRequest(BaseModel):
    symbol: str
    timeframe: str
    include_ai: bool = True
    drawings: Optional[List[str]] = None       # limit to specific drawing ids
    force_refresh: bool = False
    question: Optional[str] = None             # free-form user question for Claude


class ScreenerRow(BaseModel):
    """One line of the screener: everything needed to decide 'worth a look?'
    without opening the chart. Deliberately not the full AnalysisResult —
    scanning 30 symbols is only fast because each one skips patterns, MTF
    and Claude and asks just the rule engine for a verdict."""
    symbol: str
    display: str
    timeframe: str
    decision: Decision
    confidence: float
    quote_currency: str = "USD"
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: Optional[float] = None
    summary: str = ""
    #: When this read was generated, and the bar it was generated from — a
    #: screener result is a snapshot, not a stream, and nothing in the UI
    #: could otherwise tell "just scanned" from "scanned twenty minutes ago,
    #: before price moved past it". See docs/RISK_CONTROL_AUDIT.md's UX
    #: findings.
    generated_at: float = 0.0
    bar_time: int = 0
    error: Optional[str] = None
    #: Which app this can actually be traded on, and the exact name to search
    #: for there — "Angel One" + "NIFTY25AUG26FUT". Absent when Angel One
    #: has no contract for it right now.
    broker: Optional[str] = None
    broker_symbol: Optional[str] = None
    #: NSE, CDS, MCX, NFO — which exchange segment the Angel One contract is
    #: on. Several F&O stocks are dual-listed on BSE too; this is always the
    #: NSE side deliberately (liquidity), so it answers "which one do I pick"
    #: rather than presenting a choice.
    exchange: Optional[str] = None


class AnalysisResult(BaseModel):
    id: str
    symbol: str
    timeframe: str
    generated_at: float
    price: float
    bar_time: int
    indicators: IndicatorSnapshot
    structure: MarketStructure
    patterns: List[PatternHit] = Field(default_factory=list)
    candlestick_patterns: List[PatternHit] = Field(default_factory=list)
    mtf: MTFReport = Field(default_factory=MTFReport)
    strategies: StrategyReport = Field(default_factory=StrategyReport)
    drawings: List[DrawingAnalytics] = Field(default_factory=list)
    confidence: ConfidenceBreakdown = Field(default_factory=ConfidenceBreakdown)
    verdict: AIVerdict = Field(default_factory=AIVerdict)
    plan: TradePlan = Field(default_factory=TradePlan)
    #: How to express the plan as a listed contract. Only present for
    #: instruments that have futures and options listed.
    derivatives: Optional[DerivativeSuggestion] = None
    overlays: List[OverlayObject] = Field(default_factory=list)
    narrative: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    #: Present only when the trader has an open position on this symbol.
    management: Optional[ManagementSignal] = None
    #: Where the stop should sit on the protection ladder.
    protection: Optional[ProtectionPlan] = None
    #: Account-level limits. Always present — it is the check that
    #: matters most when there is *no* position and you are about to
    #: open one.
    account_risk: Optional[AccountRisk] = None
