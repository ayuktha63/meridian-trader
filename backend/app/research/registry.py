"""The record of every research run.

An experiment is written once: what data it ran on (by hash), what code ran
it (by fingerprint and commit), every assumption it made, how many
configurations it tried, and what it found. The record is stored in the
database for the application to query and as a file beside the datasets for
anyone else, and it is hashed — a result that has been edited afterwards no
longer matches.

The count of experiments is itself evidence. A result found on the fortieth
try is not the result it would be on the first, and anything that judges
one asks here how many there were.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DATA_DIR
from ..db.models import ExperimentRow
from . import versions


def root() -> Path:
    path = DATA_DIR / "research" / "experiments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_id(now: Optional[float] = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now or time.time()))
    return "exp-{}-{}".format(stamp, secrets.token_hex(3))


def digest(record: Dict[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key != "sha256"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def build(*, kind: str, stage: str, strategy: str, symbol: str, timeframe: str,
          venue: str, cost_profile: str, verdict: str, datasets: List[Dict[str, Any]],
          window: Dict[str, Any], parameters: Dict[str, Any], assumptions: Dict[str, Any],
          metrics: Dict[str, Any], reasons: List[str], trials: int = 1,
          seeds: Optional[Dict[str, int]] = None, extra: Optional[Dict[str, Any]] = None,
          now: Optional[float] = None) -> Dict[str, Any]:
    """Assemble a record. Nothing is stored until `save`."""
    created = time.time() if now is None else now
    record: Dict[str, Any] = {
        "id": new_id(created), "created_at": created, "kind": kind, "stage": stage,
        "strategy": {"name": strategy, "version": versions.strategy_version(strategy),
                     "parameters": parameters},
        "symbol": symbol, "timeframe": timeframe, "venue": venue,
        "cost_profile": cost_profile, "code": versions.current(), "datasets": datasets,
        "window": window, "assumptions": assumptions, "seeds": seeds or {},
        "trials": int(trials), "metrics": metrics,
        "verdict": {"state": verdict, "reasons": reasons},
    }
    record.update(extra or {})
    record["sha256"] = digest(record)
    return record


async def save(session: AsyncSession, record: Dict[str, Any]) -> Dict[str, Any]:
    """Store a record in the database and beside the datasets."""
    # Through JSON once, so what is stored is exactly what was hashed.
    record = json.loads(json.dumps(record, default=str))
    record["sha256"] = digest(record)
    (root() / (record["id"] + ".json")).write_text(
        json.dumps(record, indent=2, sort_keys=True))
    session.add(ExperimentRow(
        id=record["id"], kind=record["kind"], stage=record["stage"],
        strategy=record["strategy"]["name"],
        strategy_version=record["strategy"]["version"], symbol=record["symbol"],
        timeframe=record["timeframe"], venue=record["venue"],
        cost_profile=record["cost_profile"], trials=record["trials"],
        verdict=record["verdict"]["state"], record=record, sha256=record["sha256"],
        created_at=record["created_at"]))
    await session.flush()
    return record


def intact(record: Dict[str, Any]) -> bool:
    """Whether a record still says what it said when it was written."""
    return bool(record.get("sha256")) and digest(record) == record["sha256"]


async def get(session: AsyncSession, experiment_id: str) -> Optional[Dict[str, Any]]:
    row = await session.get(ExperimentRow, experiment_id)
    return dict(row.record) if row is not None else None


async def recent(session: AsyncSession, limit: int = 50, kind: Optional[str] = None,
                 strategy: Optional[str] = None,
                 symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    stmt = select(ExperimentRow).order_by(ExperimentRow.created_at.desc()).limit(limit)
    if kind:
        stmt = stmt.where(ExperimentRow.kind == kind)
    if strategy:
        stmt = stmt.where(ExperimentRow.strategy == strategy)
    if symbol:
        stmt = stmt.where(ExperimentRow.symbol == symbol)
    return [dict(row.record) for row in (await session.execute(stmt)).scalars().all()]


async def latest(session: AsyncSession, *, kind: str, strategy: str, symbol: str,
                 timeframe: str) -> Optional[Dict[str, Any]]:
    stmt = (select(ExperimentRow)
            .where(ExperimentRow.kind == kind, ExperimentRow.strategy == strategy,
                   ExperimentRow.symbol == symbol, ExperimentRow.timeframe == timeframe)
            .order_by(ExperimentRow.created_at.desc()).limit(1))
    row = (await session.execute(stmt)).scalars().first()
    return dict(row.record) if row is not None else None


async def trials_on_record(session: AsyncSession, since: float = 0.0,
                           kind: Optional[str] = None) -> int:
    """Configurations tried, across every experiment since `since`."""
    stmt = select(func.coalesce(func.sum(ExperimentRow.trials), 0)).where(
        ExperimentRow.created_at >= since)
    if kind:
        stmt = stmt.where(ExperimentRow.kind == kind)
    return int((await session.execute(stmt)).scalar_one())


async def runs_on_record(session: AsyncSession, *, kind: str, strategy: str, symbol: str,
                         timeframe: str, since: float = 0.0) -> int:
    """How many times this exact question has been asked since `since`."""
    stmt = select(func.count()).select_from(ExperimentRow).where(
        ExperimentRow.kind == kind, ExperimentRow.strategy == strategy,
        ExperimentRow.symbol == symbol, ExperimentRow.timeframe == timeframe,
        ExperimentRow.created_at >= since)
    return int((await session.execute(stmt)).scalar_one())
