"""Execute-live: the one endpoint that can turn a recommendation into a real
order. Everything about the trade — symbol, side, entry, stop, quantity — is
derived server-side from the stored recommendation; the request body carries
nothing but the confirmation flag, on purpose (§21 of the live-trading spec).
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .. import costs
from ..broker import live_limits
from ..broker.account_balance import effective_balance_with_source
from ..broker.angelone.execution import (
    RecommendationNotExecutable, RecommendationNotFound, angel_execution,
)
from ..data.feed import feed
from ..db.session import get_session
from ..instruments import get_instrument
from ..runtime_settings import get as get_runtime_settings
from ..schemas.execution import (
    ExecuteRequest, ExecutionPreview, ExecutionRecord, LiveTradingOverview,
)

router = APIRouter(prefix="/api/trades", tags=["execution"])


@router.get("/live", response_model=List[ExecutionRecord])
async def list_live(session: AsyncSession = Depends(get_session)) -> List[ExecutionRecord]:
    """Every live execution, newest first — open ones and the recent closed."""
    return await angel_execution.list_executions(session)


def _unrealised_pnl(record: ExecutionRecord, last_price: Optional[float]) -> Optional[float]:
    """Mark-to-market on a still-open fill: current price in, the same
    estimated round-trip charges the risk gate and the daily-loss ledger
    already use, out. `live_limits.estimated_charges` wants the ORM row's
    shape, not the schema's, so the arithmetic is redone here rather than
    bent to fit a helper that was never meant to take a price that isn't
    the stop."""
    if last_price is None or not record.average_fill_price or record.filled_quantity <= 0:
        return None
    try:
        instrument = get_instrument(record.symbol)
    except KeyError:
        return None
    sign = 1.0 if record.side == "BUY" else -1.0
    gross = (last_price - record.average_fill_price) * sign * record.filled_quantity
    charge = costs.charges(
        instrument, record.average_fill_price * record.filled_quantity,
        last_price * record.filled_quantity)
    return round(gross - charge, 2)


@router.get("/live/overview", response_model=LiveTradingOverview)
async def live_overview(session: AsyncSession = Depends(get_session)) -> LiveTradingOverview:
    """Everything the live-trading screen shows in one call: the real
    wallet, today's ledger — the same figures the pre-trade gate itself
    enforces against, not a separate guess — and every execution split into
    what's open (marked to the current price) versus history."""
    records = await angel_execution.list_executions(session, limit=500)
    ledger = await live_limits.load_ledger(session)
    balance, source = await effective_balance_with_source()

    open_positions: List[ExecutionRecord] = []
    history: List[ExecutionRecord] = []
    for record in records:
        if record.status.value in live_limits.WORKING:
            quote = feed.quote(record.symbol)
            last_price = quote.last if quote else feed.price(record.symbol)
            record.last_price = last_price
            record.unrealised_pnl = _unrealised_pnl(record, last_price)
            open_positions.append(record)
        else:
            history.append(record)

    return LiveTradingOverview(
        balance=round(balance, 2), balance_source=source,
        currency=get_runtime_settings().account_currency,
        open_risk=round(ledger.open_risk, 2),
        realised_today=round(ledger.realised_today, 2),
        trades_today=ledger.trades_today,
        open_positions=open_positions, history=history,
    )


@router.post("/{recommendation_id}/execute", response_model=ExecutionRecord)
async def execute_live(
    recommendation_id: str,
    payload: ExecuteRequest,
    session: AsyncSession = Depends(get_session),
) -> ExecutionRecord:
    try:
        return await angel_execution.execute(
            session, recommendation_id, confirmed=payload.confirmed)
    except RecommendationNotFound as exc:
        raise HTTPException(404, "No recommendation found with that id.") from exc
    except RecommendationNotExecutable as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{recommendation_id}/preview", response_model=ExecutionPreview)
async def preview_live(
    recommendation_id: str,
    session: AsyncSession = Depends(get_session),
) -> ExecutionPreview:
    """The exact order Execute Live would send, and the gate's verdict on it.
    Read-only: nothing is placed and nothing is recorded."""
    try:
        return await angel_execution.preview(session, recommendation_id)
    except RecommendationNotFound as exc:
        raise HTTPException(404, "No recommendation found with that id.") from exc
    except RecommendationNotExecutable as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{recommendation_id}/execution", response_model=ExecutionRecord)
async def get_execution(
    recommendation_id: str,
    session: AsyncSession = Depends(get_session),
) -> ExecutionRecord:
    record = await angel_execution.get_execution(session, recommendation_id)
    if record is None:
        raise HTTPException(404, "No execution recorded for that recommendation.")
    return record
