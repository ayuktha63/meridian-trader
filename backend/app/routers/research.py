"""What the research says, and the one decision it leaves to a person.

Read-only except for approvals. Nothing here runs a study — those are run
from the command line (`python -m app.research.run`) — and nothing here can
place an order or switch live trading on.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db.session import get_session
from ..instruments import get_instrument, get_timeframe
from ..research import approvals, datasets, eligibility, registry, versions
from ..services.edge import LIVE_STRATEGY, latest_edge

router = APIRouter(prefix="/api/research", tags=["research"])


def _summary(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": record["id"], "created_at": record["created_at"], "kind": record["kind"],
        "stage": record["stage"], "strategy": record["strategy"]["name"],
        "strategy_version": record["strategy"]["version"], "symbol": record["symbol"],
        "timeframe": record["timeframe"], "venue": record["venue"],
        "cost_profile": record["cost_profile"], "trials": record["trials"],
        "verdict": record["verdict"]["state"], "reasons": record["verdict"]["reasons"],
        "commit": (record.get("code") or {}).get("git", {}).get("commit"),
        "datasets": [d.get("dataset_id") for d in record.get("datasets") or []],
        "intact": registry.intact(record),
    }


@router.get("/experiments")
async def experiments(limit: int = Query(50, ge=1, le=500), kind: Optional[str] = None,
                      symbol: Optional[str] = None,
                      session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    """Every research run on record, newest first."""
    records = await registry.recent(session, limit, kind=kind,
                                    symbol=symbol.upper() if symbol else None)
    return [_summary(record) for record in records]


@router.get("/experiments/{experiment_id}")
async def experiment(experiment_id: str,
                     session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    record = await registry.get(session, experiment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No such experiment.")
    return dict(record, intact=registry.intact(record))


@router.get("/datasets")
async def dataset_catalogue() -> List[Dict[str, Any]]:
    """The history snapshots experiments ran on."""
    out = []
    for meta in datasets.catalogue():
        quality = meta.get("quality") or {}
        out.append({
            "dataset_id": meta["sha256"][:16], "sha256": meta["sha256"],
            "symbol": meta["symbol"], "timeframe": meta["timeframe"],
            "provider": meta["provider"], "bars": meta["bars"], "first": meta["first"],
            "last": meta["last"], "derived_from": meta.get("derived_from"),
            "quality_passed": bool(quality.get("passed")),
            "missing_pct": quality.get("missing_pct"),
            "largest_gap_bars": quality.get("largest_gap_bars"),
            "problems": quality.get("problems") or [],
            "warnings": quality.get("warnings") or []})
    return out


@router.get("/study")
async def latest_study(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """The most recent candidate study: what was tried and how far each got."""
    records = await registry.recent(session, 1, kind="study")
    if not records:
        return {"run": False, "families": []}
    record = records[0]
    families = []
    for family in record.get("families") or []:
        research = (family.get("stages") or {}).get("research") or {}
        pooled = research.get("pooled") or {}
        families.append({
            "key": family["key"], "strategy": family["strategy"],
            "timeframe": family["timeframe"], "profile": family["profile"],
            "state": family["state"], "reasons": family["reasons"],
            "chosen": family["chosen"], "stages_reached": list(family.get("stages") or {}),
            "research": {"trades": pooled.get("trades"),
                         "expectancy_r": pooled.get("expectancy_r"),
                         "interval": research.get("interval"),
                         "deflated_sharpe": research.get("deflated_sharpe"),
                         "break_even_cost_multiple": research.get("break_even_cost_multiple")}})
    return {"run": True, **_summary(record), "protocol": record.get("protocol"),
            "universe": record.get("datasets"), "excluded": record.get("excluded"),
            "holdout_opened_for": record.get("holdout_opened_for"),
            "by_state": (record.get("metrics") or {}).get("by_state"), "families": families}


@router.get("/eligibility")
async def eligibility_of(symbol: str, timeframe: str,
                         session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Where the live engine stands on one market, and why."""
    try:
        get_instrument(symbol.upper())
        get_timeframe(timeframe.lower())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    edge = await latest_edge(session, symbol.upper(), timeframe.lower())
    return {"symbol": symbol.upper(), "timeframe": timeframe.lower(),
            "strategy": LIVE_STRATEGY,
            "strategy_version": versions.strategy_version(LIVE_STRATEGY),
            "live_allowed": bool(edge["passed"]), "approved": bool(edge.get("approved")),
            "reason": edge.get("reason"), **edge["eligibility"],
            "states": list(eligibility.STATES), "thresholds": eligibility.THRESHOLDS}


class ApprovalRequest(BaseModel):
    symbol: str
    timeframe: str
    approved_by: str
    note: Optional[str] = None
    #: The approval is deliberate: the request has to say so.
    confirmed: bool = False


@router.get("/approvals")
async def list_approvals(session: AsyncSession = Depends(get_session)) -> List[Dict[str, Any]]:
    return await approvals.listing(session)


@router.post("/approvals")
async def approve(payload: ApprovalRequest,
                  session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """Record a person's approval of the live engine on one market. Only
    possible once every automatic check has passed; it does not switch live
    trading on, and every other pre-trade check still applies to each order."""
    symbol, timeframe = payload.symbol.upper(), payload.timeframe.lower()
    if not payload.confirmed:
        raise HTTPException(status_code=428, detail="Approval must be explicitly confirmed.")
    if not payload.approved_by.strip():
        raise HTTPException(status_code=422, detail="Say who is approving it.")
    edge = await latest_edge(session, symbol, timeframe)
    state = edge["eligibility"]
    if state["state"] != eligibility.HUMAN_REVIEW:
        raise HTTPException(status_code=409, detail={
            "message": "Not eligible for review: {}.".format(state["state"]),
            "reasons": state["reasons"]})
    row = await approvals.approve(
        session, strategy=LIVE_STRATEGY, symbol=symbol, timeframe=timeframe,
        strategy_version=versions.strategy_version(LIVE_STRATEGY),
        experiment_id=state["experiment_id"], approved_by=payload.approved_by,
        note=payload.note)
    await audit.record("strategy_approved", row.id, session, symbol=symbol,
                       timeframe=timeframe, approved_by=row.approved_by,
                       strategy_version=row.strategy_version,
                       experiment_id=row.experiment_id)
    return approvals.to_dict(row)


@router.delete("/approvals/{approval_id}")
async def revoke(approval_id: str,
                 session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    row = await approvals.revoke(session, approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such approval.")
    await audit.record("strategy_approval_revoked", row.id, session, symbol=row.symbol,
                       timeframe=row.timeframe)
    return approvals.to_dict(row)
