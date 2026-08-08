from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.feed import feed
from ..data.store import store
from ..db.session import get_session
from ..instruments import get_instrument
from ..schemas.positions import Position, PositionCreate, PositionSide, PositionUpdate
from ..services import positions as repo
from .analysis import invalidate_cache

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.get("", response_model=List[Position])
async def list_positions(
    symbol: Optional[str] = None,
    status: str = Query("open"),
    session: AsyncSession = Depends(get_session),
) -> List[Position]:
    return await repo.list_positions(session, symbol, status)


@router.post("", response_model=Position, status_code=201)
async def open_position(
    payload: PositionCreate,
    session: AsyncSession = Depends(get_session),
) -> Position:
    """Tell the platform you are in a trade so it can manage it with you."""
    try:
        instrument = get_instrument(payload.symbol)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    long = payload.side == PositionSide.LONG
    if long and payload.stop_loss >= payload.entry:
        raise HTTPException(
            status_code=422,
            detail="A long's stop must sit below the entry — otherwise it is not a stop.")
    if not long and payload.stop_loss <= payload.entry:
        raise HTTPException(
            status_code=422,
            detail="A short's stop must sit above the entry — otherwise it is not a stop.")
    if payload.take_profit is not None:
        if long and payload.take_profit <= payload.entry:
            raise HTTPException(status_code=422, detail="A long's target must be above entry")
        if not long and payload.take_profit >= payload.entry:
            raise HTTPException(status_code=422, detail="A short's target must be below entry")

    existing = await repo.open_for(session, payload.symbol)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="There is already an open {} position on {}. Close it first."
                   .format(existing.side.value, existing.symbol))

    await feed.ensure_history(instrument.symbol, payload.timeframe)
    series = store.series(instrument.symbol, payload.timeframe)
    position = await repo.open_position(session, payload, series.last_time or 0)
    await invalidate_cache(position.symbol, position.timeframe)
    return position


@router.patch("/{position_id}", response_model=Position)
async def update_position(
    position_id: str,
    payload: PositionUpdate,
    session: AsyncSession = Depends(get_session),
) -> Position:
    """Adjust a position. Stops can only be tightened, never widened."""
    position = await repo.update_position(session, position_id, payload)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    await invalidate_cache(position.symbol, position.timeframe)
    return position


@router.post("/{position_id}/close", response_model=Position)
async def close_position(
    position_id: str,
    exit_price: Optional[float] = Body(None, embed=True),
    session: AsyncSession = Depends(get_session),
) -> Position:
    existing = await repo.list_positions(session, None, "open")
    match = next((p for p in existing if p.id == position_id), None)
    price = exit_price
    if price is None and match is not None:
        price = feed.price(match.symbol) or match.entry
    if price is None:
        raise HTTPException(status_code=422, detail="exit_price is required")

    position = await repo.close_position(session, position_id, price)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    await invalidate_cache(position.symbol, position.timeframe)
    return position


@router.delete("/{position_id}", status_code=204)
async def delete_position(
    position_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    if not await repo.delete_position(session, position_id):
        raise HTTPException(status_code=404, detail="Position not found")
