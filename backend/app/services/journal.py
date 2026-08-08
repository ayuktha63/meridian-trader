"""Trade journal.

Every recommendation the platform produces can be logged with its full
evidence snapshot, then marked won or lost. That closes the loop: the accuracy
table by confidence bucket is the only honest way to find out whether the
confidence number means anything, and it is computed from the trader's own
outcomes rather than asserted.
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AnalysisRow, JournalRow
from ..schemas.analysis import AnalysisResult
from ..schemas.journal import JournalCreate, JournalEntry, JournalOutcome, JournalStats

CONFIDENCE_BUCKETS = [(0, 50, "0-50"), (50, 65, "50-65"),
                      (65, 80, "65-80"), (80, 101, "80-100")]


def _to_schema(row: JournalRow) -> JournalEntry:
    return JournalEntry(
        id=row.id, symbol=row.symbol, timeframe=row.timeframe, decision=row.decision,
        confidence=row.confidence, probability=row.probability, entry=row.entry,
        stop_loss=row.stop_loss, take_profit=row.take_profit,
        risk_reward=row.risk_reward, summary=row.summary, reasons=row.reasons or {},
        indicators=row.indicators or {}, structure=row.structure or {},
        claude_response=row.claude_response or {}, snapshot=row.snapshot,
        analysis_id=row.analysis_id, tags=row.tags or [], notes=row.notes,
        outcome=row.outcome, exit_price=row.exit_price, pnl=row.pnl,
        r_multiple=row.r_multiple, created_at=row.created_at, closed_at=row.closed_at,
    )


async def create_entry(session: AsyncSession, payload: JournalCreate) -> JournalEntry:
    row = JournalRow(
        id=uuid.uuid4().hex,
        symbol=payload.symbol.upper(),
        timeframe=payload.timeframe.lower(),
        decision=payload.decision.upper(),
        confidence=payload.confidence,
        probability=payload.probability,
        entry=payload.entry,
        stop_loss=payload.stop_loss,
        take_profit=payload.take_profit,
        risk_reward=payload.risk_reward,
        summary=payload.summary,
        reasons=payload.reasons,
        indicators=payload.indicators,
        structure=payload.structure,
        claude_response=payload.claude_response,
        snapshot=payload.snapshot,
        analysis_id=payload.analysis_id,
        tags=payload.tags,
        notes=payload.notes,
        outcome="open",
        created_at=time.time(),
    )
    session.add(row)
    await session.flush()
    return _to_schema(row)


async def from_analysis(session: AsyncSession, analysis: AnalysisResult,
                        snapshot: Optional[str] = None,
                        notes: Optional[str] = None) -> JournalEntry:
    """Log a recommendation straight from an analysis result."""
    verdict = analysis.verdict
    payload = JournalCreate(
        symbol=analysis.symbol,
        timeframe=analysis.timeframe,
        decision=verdict.decision.value,
        confidence=analysis.confidence.final_confidence,
        probability=verdict.probability,
        entry=analysis.plan.entry,
        stop_loss=analysis.plan.stop_loss,
        take_profit=analysis.plan.take_profit,
        risk_reward=analysis.plan.risk_reward,
        summary=verdict.summary,
        reasons={
            "bullish": verdict.bullish_points,
            "bearish": verdict.bearish_points,
            "invalidation": verdict.invalidation,
            "uncertainty": verdict.uncertainty,
            "confidence_components": [
                c.model_dump() for c in analysis.confidence.components
            ],
            "warnings": analysis.warnings,
        },
        indicators=analysis.indicators.model_dump(exclude={"volume_profile"}),
        structure=analysis.evidence.get("market_structure", {}),
        claude_response=verdict.model_dump(exclude={"raw"}),
        snapshot=snapshot,
        analysis_id=analysis.id,
        tags=[analysis.symbol, analysis.timeframe, verdict.decision.value,
              verdict.source],
        notes=notes,
    )
    return await create_entry(session, payload)


async def list_entries(session: AsyncSession, symbol: Optional[str] = None,
                       outcome: Optional[str] = None,
                       limit: int = 200) -> List[JournalEntry]:
    stmt = select(JournalRow)
    if symbol:
        stmt = stmt.where(JournalRow.symbol == symbol.upper())
    if outcome:
        stmt = stmt.where(JournalRow.outcome == outcome)
    stmt = stmt.order_by(JournalRow.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_schema(row) for row in rows]


async def set_outcome(session: AsyncSession, entry_id: str,
                      payload: JournalOutcome) -> Optional[JournalEntry]:
    row = await session.get(JournalRow, entry_id)
    if row is None:
        return None

    row.outcome = payload.outcome
    row.exit_price = payload.exit_price
    row.notes = payload.notes or row.notes
    row.closed_at = time.time() if payload.outcome != "open" else None

    # Derive R from the levels when the trader did not supply it.
    if payload.r_multiple is not None:
        row.r_multiple = payload.r_multiple
    elif payload.exit_price is not None and row.entry and row.stop_loss:
        risk = abs(row.entry - row.stop_loss)
        if risk > 0:
            move = (payload.exit_price - row.entry if row.decision == "BUY"
                    else row.entry - payload.exit_price)
            row.r_multiple = round(move / risk, 3)

    row.pnl = payload.pnl if payload.pnl is not None else row.pnl
    await session.flush()
    return _to_schema(row)


async def delete_entry(session: AsyncSession, entry_id: str) -> bool:
    row = await session.get(JournalRow, entry_id)
    if row is None:
        return False
    await session.delete(row)
    return True


async def stats(session: AsyncSession) -> JournalStats:
    rows = (await session.execute(select(JournalRow))).scalars().all()
    out = JournalStats(total=len(rows))
    if not rows:
        return out

    closed = [r for r in rows if r.outcome in ("win", "loss", "breakeven")]
    out.open = sum(1 for r in rows if r.outcome == "open")
    out.wins = sum(1 for r in closed if r.outcome == "win")
    out.losses = sum(1 for r in closed if r.outcome == "loss")
    out.breakeven = sum(1 for r in closed if r.outcome == "breakeven")

    r_values = [r.r_multiple for r in closed if r.r_multiple is not None]
    if closed:
        out.win_rate = round(out.wins / len(closed) * 100, 2)
    if r_values:
        out.total_r = round(sum(r_values), 2)
        out.avg_r = round(out.total_r / len(r_values), 3)
        out.best_r = round(max(r_values), 2)
        out.worst_r = round(min(r_values), 2)
        gains = sum(r for r in r_values if r > 0)
        losses = abs(sum(r for r in r_values if r < 0))
        out.profit_factor = round(gains / losses, 3) if losses else (
            999.0 if gains else 0.0)
        wins = [r for r in r_values if r > 0]
        lose = [r for r in r_values if r < 0]
        win_rate = len(wins) / len(r_values)
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(lose) / len(lose)) if lose else 0.0
        out.expectancy = round(win_rate * avg_win - (1 - win_rate) * avg_loss, 3)

    by_symbol: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "wins": 0, "losses": 0, "total_r": 0.0})
    by_decision: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "wins": 0, "losses": 0})
    buckets: Dict[str, Dict[str, Any]] = {
        name: {"total": 0, "wins": 0, "win_rate": 0.0} for _, _, name in CONFIDENCE_BUCKETS
    }

    for row in rows:
        sym = by_symbol[row.symbol]
        sym["total"] += 1
        dec = by_decision[row.decision]
        dec["total"] += 1
        if row.outcome == "win":
            sym["wins"] += 1
            dec["wins"] += 1
        elif row.outcome == "loss":
            sym["losses"] += 1
            dec["losses"] += 1
        if row.r_multiple is not None:
            sym["total_r"] = round(sym["total_r"] + row.r_multiple, 3)

        if row.outcome in ("win", "loss"):
            for lo, hi, name in CONFIDENCE_BUCKETS:
                if lo <= row.confidence < hi:
                    buckets[name]["total"] += 1
                    if row.outcome == "win":
                        buckets[name]["wins"] += 1
                    break

    for name, bucket in buckets.items():
        if bucket["total"]:
            bucket["win_rate"] = round(bucket["wins"] / bucket["total"] * 100, 2)

    out.by_symbol = dict(by_symbol)
    out.by_decision = dict(by_decision)
    out.accuracy_by_confidence = buckets
    return out


async def record_analysis(session: AsyncSession, analysis: AnalysisResult) -> None:
    """Append-only audit log of every analysis produced."""
    session.add(AnalysisRow(
        id=analysis.id,
        symbol=analysis.symbol,
        timeframe=analysis.timeframe,
        decision=analysis.verdict.decision.value,
        confidence=analysis.confidence.final_confidence,
        price=analysis.price,
        source=analysis.verdict.source,
        payload={
            "summary": analysis.verdict.summary,
            "plan": analysis.plan.model_dump(),
            "confidence": analysis.confidence.model_dump(),
            "warnings": analysis.warnings,
        },
        created_at=analysis.generated_at,
    ))
