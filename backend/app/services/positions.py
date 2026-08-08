"""Open-position storage.

The platform does not talk to a broker, so it cannot know what you hold. You
tell it. That record is what lets the management engine answer "hold or sell"
instead of only ever answering "should I enter".
"""
from __future__ import annotations

import time
import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import PositionRow
from ..schemas.positions import Position, PositionCreate, PositionSide, PositionUpdate


def _to_schema(row: PositionRow) -> Position:
    return Position(
        id=row.id, symbol=row.symbol, timeframe=row.timeframe,
        side=PositionSide(row.side), entry=row.entry, stop_loss=row.stop_loss,
        take_profit=row.take_profit, take_profit_2=row.take_profit_2,
        lots=row.lots, notes=row.notes, analysis_id=row.analysis_id,
        status=row.status, opened_at=row.opened_at,
        opened_bar_time=row.opened_bar_time, closed_at=row.closed_at,
        exit_price=row.exit_price, realised_r=row.realised_r,
        initial_stop=row.initial_stop or row.stop_loss,
        protection_tier=row.protection_tier or 0,
        peak_r=row.peak_r or 0.0, trough_r=row.trough_r or 0.0,
        broker_stop=row.broker_stop,
    )


async def list_positions(session: AsyncSession, symbol: Optional[str] = None,
                         status: str = "open") -> List[Position]:
    stmt = select(PositionRow)
    if symbol:
        stmt = stmt.where(PositionRow.symbol == symbol.upper())
    if status:
        stmt = stmt.where(PositionRow.status == status)
    stmt = stmt.order_by(PositionRow.opened_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_schema(row) for row in rows]


async def open_position(session: AsyncSession, payload: PositionCreate,
                        bar_time: int = 0) -> Position:
    row = PositionRow(
        id=uuid.uuid4().hex,
        symbol=payload.symbol.upper(),
        timeframe=payload.timeframe.lower(),
        side=payload.side.value,
        entry=payload.entry,
        stop_loss=payload.stop_loss,
        # Captured once, at entry. This is 1R for the life of the trade.
        initial_stop=payload.stop_loss,
        broker_stop=payload.stop_loss,
        take_profit=payload.take_profit,
        take_profit_2=payload.take_profit_2,
        lots=payload.lots,
        notes=payload.notes,
        analysis_id=payload.analysis_id,
        status="open",
        opened_at=time.time(),
        opened_bar_time=bar_time,
    )
    session.add(row)
    await session.flush()
    return _to_schema(row)


async def update_position(session: AsyncSession, position_id: str,
                          payload: PositionUpdate) -> Optional[Position]:
    row = await session.get(PositionRow, position_id)
    if row is None:
        return None

    # A stop may only ever be tightened. Widening one to avoid being stopped
    # out is the single most reliable way to turn a planned loss into an
    # unplanned one, so the API refuses to help with it.
    if payload.stop_loss is not None:
        long = row.side == PositionSide.LONG.value
        tighter = (payload.stop_loss > row.stop_loss) if long else (payload.stop_loss < row.stop_loss)
        if tighter:
            row.stop_loss = payload.stop_loss

    if payload.broker_stop is not None:
        row.broker_stop = payload.broker_stop
    if payload.take_profit is not None:
        row.take_profit = payload.take_profit
    if payload.take_profit_2 is not None:
        row.take_profit_2 = payload.take_profit_2
    if payload.lots is not None:
        row.lots = payload.lots
    if payload.notes is not None:
        row.notes = payload.notes

    await session.flush()
    return _to_schema(row)


async def close_position(session: AsyncSession, position_id: str,
                         exit_price: float) -> Optional[Position]:
    row = await session.get(PositionRow, position_id)
    if row is None:
        return None

    # Against the *original* stop, not the trailed one. Using the live stop
    # makes every protected trade journal as 0R (and divides by zero at
    # breakeven), which would quietly corrupt the entire calibration table.
    risk = abs(row.entry - (row.initial_stop or row.stop_loss))
    move = ((exit_price - row.entry) if row.side == PositionSide.LONG.value
            else (row.entry - exit_price))

    row.status = "closed"
    row.closed_at = time.time()
    row.exit_price = exit_price
    row.realised_r = round(move / risk, 3) if risk > 0 else 0.0
    await session.flush()
    return _to_schema(row)


async def delete_position(session: AsyncSession, position_id: str) -> bool:
    row = await session.get(PositionRow, position_id)
    if row is None:
        return False
    await session.delete(row)
    return True


async def open_for(session: AsyncSession, symbol: str) -> Optional[Position]:
    """The single open position the management engine should evaluate."""
    positions = await list_positions(session, symbol, "open")
    return positions[0] if positions else None


async def apply_protection(session: AsyncSession, position_id: str,
                           stop_loss: Optional[float] = None,
                           protection_tier: Optional[int] = None,
                           peak_r: Optional[float] = None,
                           trough_r: Optional[float] = None) -> Optional[Position]:
    """Ratchet protection forward. Never backward.

    Separate from `update_position` because this is the automatic path and it
    has stricter rules: the tier is monotonic, the water marks only extend, and
    the stop is subject to the same tighten-only guard as the manual route —
    the auto path must not be a way around it.

    `broker_stop` is deliberately left alone. The trader confirms that, and it
    is the only thing that tells them the protection on screen is real.
    """
    row = await session.get(PositionRow, position_id)
    if row is None:
        return None

    if stop_loss is not None:
        long = row.side == PositionSide.LONG.value
        tighter = (stop_loss > row.stop_loss) if long else (stop_loss < row.stop_loss)
        if tighter:
            row.stop_loss = stop_loss
    if protection_tier is not None:
        row.protection_tier = max(row.protection_tier or 0, protection_tier)
    if peak_r is not None:
        row.peak_r = max(row.peak_r or 0.0, peak_r)
    if trough_r is not None:
        row.trough_r = min(row.trough_r or 0.0, trough_r)

    await session.flush()
    return _to_schema(row)
