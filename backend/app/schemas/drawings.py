from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DrawingType(str, Enum):
    TRENDLINE = "trendline"
    HORIZONTAL = "horizontal"
    SUPPORT = "support"
    RESISTANCE = "resistance"
    RECTANGLE = "rectangle"
    SUPPLY_ZONE = "supply_zone"
    DEMAND_ZONE = "demand_zone"
    CHANNEL = "channel"
    PARALLEL_CHANNEL = "parallel_channel"
    FIB_RETRACEMENT = "fib_retracement"
    FIB_EXTENSION = "fib_extension"
    RISK_REWARD = "risk_reward"
    ARROW = "arrow"
    TEXT = "text"
    BRUSH = "brush"


#: Drawings the engine treats as an actionable price level or band.
LEVEL_TYPES = {
    DrawingType.TRENDLINE, DrawingType.HORIZONTAL, DrawingType.SUPPORT,
    DrawingType.RESISTANCE, DrawingType.CHANNEL, DrawingType.PARALLEL_CHANNEL,
}
ZONE_TYPES = {
    DrawingType.RECTANGLE, DrawingType.SUPPLY_ZONE, DrawingType.DEMAND_ZONE,
}


class Point(BaseModel):
    """Anchor in chart space — never pixels, so drawings survive zoom/pan."""

    time: int
    price: float


class DrawingStyle(BaseModel):
    color: str = "#4C8DFF"
    width: int = 2
    dash: Optional[List[int]] = None
    fill: Optional[str] = None
    opacity: float = 1.0
    extend_left: bool = False
    extend_right: bool = True
    show_labels: bool = True


class DrawingBase(BaseModel):
    symbol: str
    timeframe: str
    type: DrawingType
    points: List[Point] = Field(default_factory=list)
    style: DrawingStyle = Field(default_factory=DrawingStyle)
    label: Optional[str] = None
    text: Optional[str] = None
    locked: bool = False
    visible: bool = True
    meta: Dict[str, Any] = Field(default_factory=dict)


class DrawingCreate(DrawingBase):
    client_id: Optional[str] = None


class DrawingUpdate(BaseModel):
    points: Optional[List[Point]] = None
    style: Optional[DrawingStyle] = None
    label: Optional[str] = None
    text: Optional[str] = None
    locked: Optional[bool] = None
    visible: Optional[bool] = None
    meta: Optional[Dict[str, Any]] = None


class TouchEvent(BaseModel):
    time: int
    price: float
    index: int
    kind: str          # touch | rejection | break | retest | fake_break
    distance_pips: float
    body_close_beyond: bool = False
    volume_ratio: float = 1.0
    reaction_pips: float = 0.0
    wick_ratio: float = 0.0


class DrawingAnalytics(BaseModel):
    """Everything the AI needs to reason about a user-drawn object."""

    drawing_id: str
    type: DrawingType
    role: str = "neutral"                    # resistance | support | zone | measurement
    anchor_start: Optional[Point] = None
    anchor_end: Optional[Point] = None
    current_value: Optional[float] = None    # projected level at the live bar
    upper_value: Optional[float] = None      # zones/channels
    lower_value: Optional[float] = None
    slope_per_bar: float = 0.0
    angle_degrees: float = 0.0
    length_bars: int = 0
    age_bars: int = 0
    touches: int = 0
    reactions: int = 0
    breaks: int = 0
    fake_breaks: int = 0
    retests: int = 0
    last_touch_bars_ago: Optional[int] = None
    distance_pips: float = 0.0
    distance_atr: float = 0.0
    approaching: bool = False
    price_side: str = "unknown"              # above | below | inside
    strength: float = 0.0                    # 0-100 composite reliability
    validity: str = "untested"               # untested | respected | broken | invalidated
    volume_at_touches: float = 1.0
    events: List[TouchEvent] = Field(default_factory=list)
    fib_levels: Dict[str, float] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)


class Drawing(DrawingBase):
    id: str
    created_at: float
    updated_at: float
    analytics: Optional[DrawingAnalytics] = None
