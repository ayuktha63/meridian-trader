from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.feed import feed
from ..data.store import store
from ..db.session import get_session
from ..instruments import get_instrument
from ..analysis import drawings as drawing_engine
from ..analysis import indicators
from ..schemas.drawings import (
    Drawing, DrawingAnalytics, DrawingCreate, DrawingUpdate,
)
from ..services import drawings as repo
from .analysis import invalidate_cache

router = APIRouter(prefix="/api/drawings", tags=["drawings"])


@router.get("", response_model=List[Drawing])
async def list_drawings(
    symbol: str = Query(...),
    timeframe: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
) -> List[Drawing]:
    return await repo.list_drawings(session, symbol, timeframe)


@router.post("", response_model=Drawing, status_code=201)
async def create_drawing(
    payload: DrawingCreate,
    session: AsyncSession = Depends(get_session),
) -> Drawing:
    if len(payload.points) == 0:
        raise HTTPException(status_code=422, detail="A drawing needs at least one anchor")
    drawing = await repo.create_drawing(session, payload)
    await invalidate_cache(drawing.symbol, drawing.timeframe)
    return drawing


@router.patch("/{drawing_id}", response_model=Drawing)
async def update_drawing(
    drawing_id: str,
    payload: DrawingUpdate,
    session: AsyncSession = Depends(get_session),
) -> Drawing:
    drawing = await repo.update_drawing(session, drawing_id, payload)
    if drawing is None:
        raise HTTPException(status_code=404, detail="Drawing not found")
    await invalidate_cache(drawing.symbol, drawing.timeframe)
    return drawing


@router.delete("/{drawing_id}", status_code=204)
async def delete_drawing(
    drawing_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    existing = await repo.get_drawing(session, drawing_id)
    if not await repo.delete_drawing(session, drawing_id):
        raise HTTPException(status_code=404, detail="Drawing not found")
    if existing is not None:
        await invalidate_cache(existing.symbol, existing.timeframe)


@router.delete("", status_code=200)
async def clear_drawings(
    symbol: str = Query(...),
    timeframe: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    removed = await repo.clear_drawings(session, symbol, timeframe)
    await invalidate_cache(symbol, timeframe or "15m")
    for code in ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"):
        await invalidate_cache(symbol, code)
    return {"removed": removed}


@router.get("/analytics", response_model=List[DrawingAnalytics])
async def drawing_analytics(
    symbol: str = Query(...),
    timeframe: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> List[DrawingAnalytics]:
    """Touch counts, reactions, breaks and projections for the trader's drawings."""
    items = await repo.list_drawings(session, symbol, timeframe)
    if not items:
        return []

    await feed.ensure_history(symbol, timeframe)
    df = store.frame(symbol, timeframe, 600)
    if df is None or df.empty:
        raise HTTPException(status_code=503, detail="No price data to analyse against")

    instrument = get_instrument(symbol)
    atr_series = indicators.atr(df, 14)
    atr = float(atr_series.iloc[-1]) if len(atr_series.dropna()) else float(
        (df["high"] - df["low"]).tail(30).mean())
    return drawing_engine.analyse_many(items, df, instrument, atr or 1e-6)
