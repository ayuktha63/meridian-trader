from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..backtest import run_backtest, walk_forward
from ..research.datasets import DatasetError
from ..research.engine_evidence import walk_forward_on_snapshot
from ..db.models import BacktestRow
from ..db.session import get_session
from ..instruments import get_instrument, get_timeframe
from ..schemas.journal import BacktestRequest, BacktestResult
from ..services.edge import WALK_FORWARD_PREFIX, latest_edge

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
        payload={"request": payload.model_dump(), "notes": result.notes,
                 "edge": result.edge.model_dump(), "data_source": result.data_source},
    ))
    return result


@router.post("/walk-forward", response_model=BacktestResult)
async def run_walk_forward(
    payload: BacktestRequest,
    session: AsyncSession = Depends(get_session),
) -> BacktestResult:
    """The evidence the live gate asks for: out-of-sample, after costs."""
    try:
        get_instrument(payload.symbol)
        get_timeframe(payload.timeframe)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        # A snapshot from the venue: the same test can then be run again on
        # the same bars, and it goes on record as an experiment.
        record = await walk_forward_on_snapshot(
            session, payload.symbol, payload.timeframe, bars=payload.bars,
            cost_profile=payload.cost_profile, strategy=payload.strategy, request=payload)
        return record["result"]
    except DatasetError:
        pass                     # no real venue serves it: simulated, and said to be

    result = await walk_forward(payload)
    session.add(BacktestRow(
        id=uuid.uuid4().hex, symbol=result.symbol, timeframe=result.timeframe,
        strategy=WALK_FORWARD_PREFIX + result.strategy, bars=result.bars_tested,
        metrics=result.metrics.model_dump(),
        payload={"request": payload.model_dump(), "notes": result.notes,
                 "edge": result.edge.model_dump(), "folds": result.folds,
                 "next_parameters": result.next_parameters,
                 "data_source": result.data_source},
    ))
    return result


@router.get("/edge")
async def measured_edge(symbol: str, timeframe: str,
                        session: AsyncSession = Depends(get_session)) -> dict:
    """What the live gate currently believes about this symbol and timeframe."""
    return await latest_edge(session, symbol.upper(), timeframe.lower())
