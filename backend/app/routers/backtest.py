from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..backtest import run_backtest
from ..db.models import BacktestRow
from ..db.session import get_session
from ..instruments import get_instrument, get_timeframe
from ..schemas.journal import BacktestRequest, BacktestResult

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.post("/run", response_model=BacktestResult)
async def run(
    payload: BacktestRequest,
    session: AsyncSession = Depends(get_session),
) -> BacktestResult:
    try:
        get_instrument(payload.symbol)
        get_timeframe(payload.timeframe)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    result = await run_backtest(payload)

    session.add(BacktestRow(
        id=uuid.uuid4().hex,
        symbol=result.symbol,
        timeframe=result.timeframe,
        strategy=result.strategy,
        bars=result.bars_tested,
        metrics=result.metrics.model_dump(),
        payload={"request": payload.model_dump(), "notes": result.notes},
    ))
    return result
