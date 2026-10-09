"""The go-live evidence: has this symbol and timeframe shown an edge?

Only a walk-forward run counts — out-of-sample by construction and net of
costs — and only a recent one, because an edge measured on last quarter's
market is a claim about last quarter.
"""
from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import BacktestRow

WALK_FORWARD_PREFIX = "walk_forward:"
MAX_AGE_SECONDS = 30 * 86_400

NOT_MEASURED = ("No measured edge: no walk-forward test has been run for {} on {}. "
                "Run one from the Backtest tab.")


async def latest_edge(session: AsyncSession, symbol: str, timeframe: str) -> dict:
    stmt = (select(BacktestRow)
            .where(BacktestRow.symbol == symbol, BacktestRow.timeframe == timeframe,
                   BacktestRow.strategy.like(WALK_FORWARD_PREFIX + "%"))
            .order_by(BacktestRow.created_at.desc()).limit(1))
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return {"passed": False, "reason": NOT_MEASURED.format(symbol, timeframe)}
    edge = dict((row.payload or {}).get("edge") or {})
    edge["measured_at"] = row.created_at
    if time.time() - (row.created_at or 0) > MAX_AGE_SECONDS:
        edge["passed"] = False
        edge["reason"] = ("The last walk-forward test for {} on {} is more than 30 days "
                          "old — run it again.".format(symbol, timeframe))
    edge.setdefault("passed", False)
    edge.setdefault("reason", "No verdict recorded.")
    return edge
