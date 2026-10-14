"""A person's decision, on record.

Nothing a test produces can approve a strategy for live trading. When every
automatic check has passed, the state is ELIGIBLE FOR HUMAN REVIEW, and the
live gate still refuses until someone has looked and said yes — for that
strategy version, on that market and timeframe. Change the code and the
approval is for something that no longer exists; it stops counting, and the
new version needs its own.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import StrategyApprovalRow


def to_dict(row: StrategyApprovalRow) -> Dict[str, Any]:
    return {"id": row.id, "strategy": row.strategy, "strategy_version": row.strategy_version,
            "symbol": row.symbol, "timeframe": row.timeframe,
            "experiment_id": row.experiment_id, "approved_by": row.approved_by,
            "note": row.note, "created_at": row.created_at, "revoked_at": row.revoked_at}


async def active(session: AsyncSession, strategy: str, symbol: str, timeframe: str,
                 strategy_version: str) -> Optional[StrategyApprovalRow]:
    """The standing approval for exactly this version on this market."""
    stmt = (select(StrategyApprovalRow)
            .where(StrategyApprovalRow.strategy == strategy,
                   StrategyApprovalRow.symbol == symbol.upper(),
                   StrategyApprovalRow.timeframe == timeframe.lower(),
                   StrategyApprovalRow.strategy_version == strategy_version,
                   StrategyApprovalRow.revoked_at.is_(None))
            .order_by(StrategyApprovalRow.created_at.desc()).limit(1))
    return (await session.execute(stmt)).scalars().first()


async def approve(session: AsyncSession, *, strategy: str, strategy_version: str,
                  symbol: str, timeframe: str, experiment_id: str, approved_by: str,
                  note: Optional[str] = None) -> StrategyApprovalRow:
    row = StrategyApprovalRow(
        strategy=strategy, strategy_version=strategy_version, symbol=symbol.upper(),
        timeframe=timeframe.lower(), experiment_id=experiment_id,
        approved_by=approved_by.strip(), note=(note or "").strip() or None)
    session.add(row)
    await session.flush()
    return row


async def revoke(session: AsyncSession, approval_id: str) -> Optional[StrategyApprovalRow]:
    row = await session.get(StrategyApprovalRow, approval_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = time.time()
        await session.flush()
    return row


async def listing(session: AsyncSession, limit: int = 50) -> List[Dict[str, Any]]:
    stmt = (select(StrategyApprovalRow)
            .order_by(StrategyApprovalRow.created_at.desc()).limit(limit))
    return [to_dict(row) for row in (await session.execute(stmt)).scalars().all()]
