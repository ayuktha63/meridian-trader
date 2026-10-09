"""Demo-account routes.

The auto-trader runs server-side inside the analysis and tick loops; these
endpoints only read its state and let you arm, disarm or reset it.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .. import runtime_settings
from ..data.feed import feed
from ..db.session import get_session
from ..instruments import get_instrument
from ..schemas.paper import ExitReason, PaperState, PaperTrade
from ..services import paper as repo

router = APIRouter(prefix="/api/paper", tags=["paper"])


class AutoConfig(BaseModel):
    enabled: Optional[bool] = None
    risk_per_trade_pct: Optional[float] = None
    min_confidence: Optional[float] = None
    symbols: Optional[List[str]] = None
    timeframes: Optional[List[str]] = None


class ResetRequest(BaseModel):
    starting_balance: float = 10_000.0
    currency: Optional[str] = None


async def _state(session: AsyncSession) -> PaperState:
    cfg = runtime_settings.get()
    account = await repo.get_account(session, currency=cfg.account_currency)
    open_trade = await repo.open_trade_for(session)
    closed = await repo.list_trades(session, status="closed", limit=200)

    price = feed.price(open_trade.symbol) if open_trade else None
    account = repo.summarise(account, closed, open_trade, price)
    return PaperState(account=account, open_trade=open_trade,
                      recent=closed[:15])


@router.get("", response_model=PaperState)
async def get_state(session: AsyncSession = Depends(get_session)) -> PaperState:
    return await _state(session)


@router.patch("/config", response_model=PaperState)
async def update_config(payload: AutoConfig,
                        session: AsyncSession = Depends(get_session)) -> PaperState:
    """Arm or disarm the auto-trader and tune its gates."""
    if payload.min_confidence is not None and not 0 <= payload.min_confidence <= 100:
        raise HTTPException(422, "min_confidence must be between 0 and 100")
    if payload.risk_per_trade_pct is not None and not 0 < payload.risk_per_trade_pct <= 10:
        raise HTTPException(
            422, "risk_per_trade_pct must be between 0 and 10 — above 10% per "
                 "trade a normal losing streak ends the account")
    await repo.update_account(
        session, enabled=payload.enabled,
        risk_per_trade_pct=payload.risk_per_trade_pct,
        min_confidence=payload.min_confidence, symbols=payload.symbols,
        timeframes=payload.timeframes)
    return await _state(session)


@router.post("/reset", response_model=PaperState)
async def reset(payload: ResetRequest = Body(default=ResetRequest()),
                session: AsyncSession = Depends(get_session)) -> PaperState:
    """Start the demo over. Deletes every paper trade."""
    cfg = runtime_settings.get()
    await repo.reset_account(session, payload.starting_balance,
                             payload.currency or cfg.account_currency)
    return await _state(session)


@router.post("/close/{trade_id}", response_model=PaperState)
async def close(trade_id: str,
                session: AsyncSession = Depends(get_session)) -> PaperState:
    """Close an open paper trade by hand."""
    trade = await repo.open_trade_for(session)
    if trade is None or trade.id != trade_id:
        raise HTTPException(404, "No such open paper trade")
    quote = feed.quote(trade.symbol)
    price = feed.price(trade.symbol) or trade.entry
    await repo.close_trade(
        session, trade_id,
        quote.bid if quote else price, quote.ask if quote else price, price,
        ExitReason.MANUAL, get_instrument(trade.symbol))
    return await _state(session)


@router.get("/trades", response_model=List[PaperTrade])
async def list_trades(status: Optional[str] = None, limit: int = 50,
                      session: AsyncSession = Depends(get_session)) -> List[PaperTrade]:
    return await repo.list_trades(session, status, limit)
