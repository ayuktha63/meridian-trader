"""Execute-live: the one endpoint that can turn a recommendation into a real
order. Everything about the trade — symbol, side, entry, stop, quantity — is
derived server-side from the stored recommendation; the request body carries
nothing but the confirmation flag, on purpose (§21 of the live-trading spec).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..broker.angelone.execution import (
    RecommendationNotExecutable, RecommendationNotFound, angel_execution,
)
from ..db.session import get_session
from ..schemas.execution import ExecuteRequest, ExecutionRecord

router = APIRouter(prefix="/api/trades", tags=["execution"])


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


@router.get("/{recommendation_id}/execution", response_model=ExecutionRecord)
async def get_execution(
    recommendation_id: str,
    session: AsyncSession = Depends(get_session),
) -> ExecutionRecord:
    record = await angel_execution.get_execution(session, recommendation_id)
    if record is None:
        raise HTTPException(404, "No execution recorded for that recommendation.")
    return record
