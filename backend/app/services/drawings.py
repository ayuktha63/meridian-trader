"""Drawing persistence.

Drawings are the trader's own analysis, so they survive restarts, timeframe
switches and browser reloads. They are stored per symbol and timeframe: a
level drawn on the 4H is not automatically a level on the 1m, and pretending
otherwise would put noise into the AI brief.
"""
from __future__ import annotations

import time
import uuid
from typing import List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import DrawingRow
from ..schemas.drawings import Drawing, DrawingCreate, DrawingStyle, DrawingUpdate, Point


def _to_schema(row: DrawingRow) -> Drawing:
    return Drawing(
        id=row.id,
        symbol=row.symbol,
        timeframe=row.timeframe,
        type=row.type,
        points=[Point(**p) for p in (row.points or [])],
        style=DrawingStyle(**(row.style or {})),
        label=row.label,
        text=row.text,
        locked=bool(row.locked),
        visible=bool(row.visible),
        meta=row.meta or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def list_drawings(session: AsyncSession, symbol: str,
                        timeframe: Optional[str] = None) -> List[Drawing]:
    stmt = select(DrawingRow).where(DrawingRow.symbol == symbol.upper())
    if timeframe:
        stmt = stmt.where(DrawingRow.timeframe == timeframe.lower())
    stmt = stmt.order_by(DrawingRow.created_at.asc())
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_schema(row) for row in rows]


async def get_drawing(session: AsyncSession, drawing_id: str) -> Optional[Drawing]:
    row = await session.get(DrawingRow, drawing_id)
    return _to_schema(row) if row else None


async def create_drawing(session: AsyncSession, payload: DrawingCreate) -> Drawing:
    now = time.time()
    row = DrawingRow(
        id=uuid.uuid4().hex,
        symbol=payload.symbol.upper(),
        timeframe=payload.timeframe.lower(),
        type=payload.type.value,
        points=[p.model_dump() for p in payload.points],
        style=payload.style.model_dump(),
        label=payload.label,
        text=payload.text,
        locked=payload.locked,
        visible=payload.visible,
        meta=payload.meta,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    return _to_schema(row)


async def update_drawing(session: AsyncSession, drawing_id: str,
                         payload: DrawingUpdate) -> Optional[Drawing]:
    row = await session.get(DrawingRow, drawing_id)
    if row is None:
        return None
    if payload.points is not None:
        row.points = [p.model_dump() for p in payload.points]
    if payload.style is not None:
        row.style = payload.style.model_dump()
    if payload.label is not None:
        row.label = payload.label
    if payload.text is not None:
        row.text = payload.text
    if payload.locked is not None:
        row.locked = payload.locked
    if payload.visible is not None:
        row.visible = payload.visible
    if payload.meta is not None:
        row.meta = payload.meta
    row.updated_at = time.time()
    await session.flush()
    return _to_schema(row)


async def delete_drawing(session: AsyncSession, drawing_id: str) -> bool:
    row = await session.get(DrawingRow, drawing_id)
    if row is None:
        return False
    await session.delete(row)
    return True


async def clear_drawings(session: AsyncSession, symbol: str,
                         timeframe: Optional[str] = None) -> int:
    stmt = delete(DrawingRow).where(DrawingRow.symbol == symbol.upper())
    if timeframe:
        stmt = stmt.where(DrawingRow.timeframe == timeframe.lower())
    result = await session.execute(stmt)
    return int(result.rowcount or 0)
