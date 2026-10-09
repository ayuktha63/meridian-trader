"""Persistence models. Runs on Postgres (asyncpg) or SQLite (aiosqlite)."""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import Boolean, Float, Index, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> float:
    return time.time()


class Base(DeclarativeBase):
    pass


class DrawingRow(Base):
    __tablename__ = "drawings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    type: Mapped[str] = mapped_column(String(32))
    points: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    style: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    label: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    meta: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=_now)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)

    __table_args__ = (Index("ix_drawings_symbol_tf", "symbol", "timeframe"),)


class JournalRow(Base):
    __tablename__ = "journal"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    decision: Mapped[str] = mapped_column(String(8), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    probability: Mapped[float] = mapped_column(Float, default=0.0)
    entry: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    risk_reward: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    reasons: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    indicators: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    structure: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    claude_response: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    analysis_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tags: Mapped[List[str]] = mapped_column(JSON, default=list)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), default="open", index=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    closed_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class AnalysisRow(Base):
    """Append-only log of every analysis produced — feeds journal + audit."""

    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    decision: Mapped[str] = mapped_column(String(8))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(16), default="claude")
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)


class PositionRow(Base):
    """A trade the user says they are actually in.

    Nothing here places or mirrors a broker order — it is the user telling the
    platform what they hold so the management engine can answer "hold or sell".
    """

    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    side: Mapped[str] = mapped_column(String(8))
    entry: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    #: The stop as it was at entry. This is the **risk unit** — 1R — and it
    #: must never change, because every R figure in the platform is measured
    #: against it. Deriving R from the live stop instead looks harmless until
    #: the stop trails: at breakeven the risk becomes zero, and a trade that
    #: made +3R journals as 0R or divides by zero.
    initial_stop: Mapped[float] = mapped_column(Float, default=0.0)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit_2: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lots: Mapped[float] = mapped_column(Float, default=1.0)

    #: Highest protection tier the trade has reached. Monotonic — protection
    #: ratchets and never unwinds.
    protection_tier: Mapped[int] = mapped_column(Integer, default=0)
    #: High-water and low-water marks in R, updated from ticks rather than bar
    #: closes, so an intrabar spike is not lost.
    peak_r: Mapped[float] = mapped_column(Float, default=0.0)
    trough_r: Mapped[float] = mapped_column(Float, default=0.0)
    #: The stop the trader has confirmed is actually resting at their broker.
    #: When this lags `stop_loss`, the protection on screen is imaginary — and
    #: that gap is the most dangerous state a tracked position can be in.
    broker_stop: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    analysis_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    opened_at: Mapped[float] = mapped_column(Float, default=_now)
    opened_bar_time: Mapped[int] = mapped_column(Integer, default=0)
    closed_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    realised_r: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class BacktestRow(Base):
    __tablename__ = "backtests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    strategy: Mapped[str] = mapped_column(String(32))
    bars: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)


class PaperAccountRow(Base):
    """The demo account the auto-trader runs on.

    Deliberately a single row keyed "demo": one account keeps the performance
    table meaningful. Nothing here touches a broker or real money.
    """

    __tablename__ = "paper_accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="demo")
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    starting_balance: Mapped[float] = mapped_column(Float, default=10_000.0)
    balance: Mapped[float] = mapped_column(Float, default=10_000.0)

    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=1.0)
    min_confidence: Mapped[float] = mapped_column(Float, default=55.0)
    #: Comma-separated. Empty means "whatever chart is being watched".
    symbols: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: Comma-separated. Empty means "any timeframe". Restricting this matters:
    #: backtesting showed the confluence engine's edge is timeframe-dependent
    #: (positive on 1h, negative on 15m for most symbols tested).
    timeframes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    peak_equity: Mapped[float] = mapped_column(Float, default=10_000.0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[float] = mapped_column(Float, default=_now)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)


class PaperTradeRow(Base):
    """One simulated trade, filled at live prices with real costs charged."""

    __tablename__ = "paper_trades"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(String(32), index=True, default="demo")
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    side: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)

    entry: Mapped[float] = mapped_column(Float)
    entry_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    stop_loss: Mapped[float] = mapped_column(Float)
    initial_stop: Mapped[float] = mapped_column(Float, default=0.0)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    notional: Mapped[float] = mapped_column(Float, default=0.0)

    entry_cost: Mapped[float] = mapped_column(Float, default=0.0)
    exit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realised_r: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    peak_r: Mapped[float] = mapped_column(Float, default=0.0)
    trough_r: Mapped[float] = mapped_column(Float, default=0.0)
    protection_tier: Mapped[int] = mapped_column(Integer, default=0)

    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    decision_source: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    analysis_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class AuditRow(Base):
    """Append-only record of everything that touched real money: mode and
    kill-switch changes, every pre-trade decision, every order and close.
    Nothing in the app updates or deletes a row here."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    event: Mapped[str] = mapped_column(String(40), index=True)
    subject: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    detail: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)


class LiveExecutionRow(Base):
    """A real Angel One order and what became of it.

    Deliberately separate from `AnalysisRow` (the recommendation) and from
    `PositionRow`/`PaperTradeRow` (paper tracking) — a recommendation is not
    an order, and a submitted order is not a fill. This table only ever
    represents what Angel One actually said, never what Meridian intended.
    One row per recommendation at most: `recommendation_id` is unique, which
    is the first, simplest layer of duplicate-order protection.
    """

    __tablename__ = "live_executions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    recommendation_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    broker: Mapped[str] = mapped_column(String(16), default="ANGEL_ONE")

    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    exchange: Mapped[str] = mapped_column(String(8))
    trading_symbol: Mapped[str] = mapped_column(String(64))
    instrument_token: Mapped[str] = mapped_column(String(32))

    requested_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    requested_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_fill_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    target_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    risk_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    #: See schemas.execution.ExecutionStatus. Stored as plain text rather than
    #: a DB enum so a new status added later never needs a migration.
    status: Mapped[str] = mapped_column(String(24), default="VALIDATING", index=True)
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: Idempotency tag sent as `ordertag` on the entry (see broker.angelone.orders).
    order_tag: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    tick_size: Mapped[float] = mapped_column(Float, default=0.05)
    #: The stop as first placed — the risk unit, never changed (cf. PositionRow).
    initial_stop: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    protection_tier: Mapped[int] = mapped_column(Integer, default=0)
    #: Broker-side protective orders. NONE until a fill; PROTECTED once a stop
    #: rests at the broker for the full filled quantity; FAILED means the
    #: stop could not be placed and the position was flattened.
    protection_status: Mapped[str] = mapped_column(String(16), default="NONE")
    stop_order_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    target_order_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    protected_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    exit_order_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    gross_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    closed_at: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)

    created_at: Mapped[float] = mapped_column(Float, default=_now)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)
