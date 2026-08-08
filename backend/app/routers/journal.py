from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.session import get_session
from ..schemas.analysis import AnalysisResult
from ..schemas.journal import (
    JournalCreate, JournalEntry, JournalOutcome, JournalStats,
)
from ..services import journal as service

router = APIRouter(prefix="/api/journal", tags=["journal"])


@router.get("", response_model=List[JournalEntry])
async def list_entries(
    symbol: Optional[str] = None,
    outcome: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> List[JournalEntry]:
    return await service.list_entries(session, symbol, outcome, limit)


@router.post("", response_model=JournalEntry, status_code=201)
async def create_entry(
    payload: JournalCreate,
    session: AsyncSession = Depends(get_session),
) -> JournalEntry:
    return await service.create_entry(session, payload)


@router.post("/from-analysis", response_model=JournalEntry, status_code=201)
async def log_analysis(
    analysis: AnalysisResult = Body(..., embed=True),
    snapshot: Optional[str] = Body(None, embed=True),
    notes: Optional[str] = Body(None, embed=True),
    session: AsyncSession = Depends(get_session),
) -> JournalEntry:
    """Log a recommendation with its full evidence snapshot and chart image."""
    return await service.from_analysis(session, analysis, snapshot, notes)


@router.patch("/{entry_id}/outcome", response_model=JournalEntry)
async def set_outcome(
    entry_id: str,
    payload: JournalOutcome,
    session: AsyncSession = Depends(get_session),
) -> JournalEntry:
    entry = await service.set_outcome(session, entry_id, payload)
    if entry is None:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return entry


@router.delete("/{entry_id}", status_code=204)
async def delete_entry(
    entry_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    if not await service.delete_entry(session, entry_id):
        raise HTTPException(status_code=404, detail="Journal entry not found")


@router.get("/stats", response_model=JournalStats)
async def stats(session: AsyncSession = Depends(get_session)) -> JournalStats:
    return await service.stats(session)
