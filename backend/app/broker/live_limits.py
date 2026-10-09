"""The live account's own trade history, and the limits checked against it.

The paper guardrails (`analysis/guardrails.py`) read the paper ledger, so
they could never bind a real order — "check 11" of the pre-trade gate was
explicitly missing. `live_executions` is now kept current by the reconciler,
which makes it the ledger these limits need: what is open at the broker,
what was traded today, what today has cost, and what today could still cost
if every open stop were hit.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import LiveExecutionRow
from ..schemas.execution import ExecutionStatus as S
from .angelone.orders import IST

#: Reached the broker and is not finished: counts as open exposure whether
#: or not it has filled yet, because it can.
WORKING = (S.SUBMITTING.value, S.UNKNOWN.value, S.SUBMITTED.value, S.OPEN.value,
           S.PARTIALLY_FILLED.value, S.CANCEL_PENDING.value, S.FILLED.value,
           S.CLOSING.value)


def trading_day_start(now: Optional[float] = None) -> float:
    """Midnight IST. The exchanges this account trades keep Indian hours, so
    a day that rolled over at 00:00 UTC would split one session in two."""
    moment = datetime.fromtimestamp(now or time.time(), IST)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def sector_of(symbol: str) -> Optional[str]:
    from ..stocks import NSE_FO_STOCKS
    for row in NSE_FO_STOCKS:
        if row[0] == symbol:
            return row[2]
    return None


def open_risk(row: LiveExecutionRow) -> float:
    """Cash this execution loses if its stop is hit from here. Measured from
    the entry, so a stop ratcheted to breakeven or better risks nothing —
    and an order still unfilled risks what it would if it filled."""
    quantity = max((row.filled_quantity or 0.0) - (row.exit_quantity or 0.0), 0.0)
    if (row.filled_quantity or 0.0) <= 0:
        quantity = row.requested_quantity or 0.0
    entry = row.average_fill_price or row.requested_price
    if not entry or not row.stop_price or quantity <= 0:
        return 0.0
    sign = 1.0 if row.side == "BUY" else -1.0
    return max((entry - row.stop_price) * sign, 0.0) * quantity


@dataclass
class LiveLedger:
    open_positions: int = 0
    trades_today: int = 0
    realised_today: float = 0.0
    #: What the open book loses if every stop is hit.
    open_risk: float = 0.0
    symbol_exposure: Dict[str, float] = field(default_factory=dict)
    sector_exposure: Dict[str, float] = field(default_factory=dict)


async def load_ledger(session: AsyncSession, now: Optional[float] = None,
                      exclude_id: Optional[str] = None) -> LiveLedger:
    day = trading_day_start(now)
    ledger = LiveLedger()
    stmt = select(LiveExecutionRow).where(or_(
        LiveExecutionRow.status.in_(WORKING),
        LiveExecutionRow.created_at >= day,
        LiveExecutionRow.submitted_at >= day,
        LiveExecutionRow.closed_at >= day,
    ))
    for row in (await session.execute(stmt)).scalars().all():
        if row.id == exclude_id:
            continue
        if row.status in WORKING:
            ledger.open_positions += 1
            ledger.open_risk += open_risk(row)
            quantity = max(row.filled_quantity or 0.0, row.requested_quantity or 0.0)
            value = quantity * (row.average_fill_price or row.requested_price or 0.0)
            ledger.symbol_exposure[row.symbol] = ledger.symbol_exposure.get(row.symbol, 0.0) + value
            sector = sector_of(row.symbol)
            if sector:
                ledger.sector_exposure[sector] = ledger.sector_exposure.get(sector, 0.0) + value
        sent_at = row.submitted_at or row.created_at or 0.0
        if row.broker_order_id and sent_at >= day:
            ledger.trades_today += 1
        if row.closed_at and row.closed_at >= day:
            ledger.realised_today += row.gross_pnl or 0.0
    return ledger
