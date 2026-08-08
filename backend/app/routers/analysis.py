from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import claude_client
from ..analysis import engine
from ..cache import cache
from ..config import settings
from ..db.session import get_session, session_scope
from ..instruments import DEFAULT_STREAM_SYMBOLS, get_instrument, get_timeframe
from ..schemas.analysis import AnalysisRequest, AnalysisResult
from ..services import drawings as drawing_repo
from .. import runtime_settings
from ..services import positions as position_repo


async def _account_risk(session):
    """Account-level limits, priced against live quotes where available."""
    from ..analysis import guardrails
    from ..data.feed import feed as market_feed

    cfg = runtime_settings.get()
    open_positions = await position_repo.list_positions(session, None, "open")
    closed = await position_repo.list_positions(session, None, "closed")
    prices = {p.symbol: market_feed.price(p.symbol) for p in open_positions}
    return guardrails.evaluate(
        open_positions, closed, cfg.account_balance, cfg.risk_per_trade_pct,
        cfg.account_currency, {k: v for k, v in prices.items() if v},
    )
from ..services import journal as journal_service

log = logging.getLogger("meridian.api.analysis")
router = APIRouter(prefix="/api/analysis", tags=["analysis"])

#: Analyses are expensive; identical requests inside this window are served
#: from cache so a chatty UI cannot trigger repeated Claude calls.
CACHE_TTL_SECONDS = 20

_locks: Dict[str, asyncio.Lock] = {}


def _lock_for(key: str) -> asyncio.Lock:
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


def _cache_key(symbol: str, timeframe: str, include_ai: bool, has_question: bool) -> str:
    return "analysis:{}:{}:{}:{}".format(
        symbol.upper(), timeframe.lower(), int(include_ai), has_question)


async def invalidate_cache(symbol: str, timeframe: str) -> None:
    """Drop cached analyses for a view.

    Called whenever the trader's drawings change: the drawings are part of the
    evidence, so a cached verdict computed without them is stale the moment a
    level is added, moved or deleted.
    """
    for include_ai in (True, False):
        await cache.delete(_cache_key(symbol, timeframe, include_ai, False))


async def _run(symbol: str, timeframe: str, session: AsyncSession,
               include_ai: bool, force: bool,
               question: Optional[str] = None) -> AnalysisResult:
    try:
        get_instrument(symbol)
        get_timeframe(timeframe)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    key = _cache_key(symbol, timeframe, include_ai, bool(question))

    if not force and not question:
        cached = await cache.get_json(key)
        if cached:
            return AnalysisResult(**cached)

    # One in-flight analysis per view — concurrent callers share the result.
    async with _lock_for(key):
        if not force and not question:
            cached = await cache.get_json(key)
            if cached:
                result = AnalysisResult(**cached)
                result.account_risk = await _account_risk(session)
                return result

        drawings = await drawing_repo.list_drawings(session, symbol, timeframe)
        position = await position_repo.open_for(session, symbol)
        result = await engine.run(symbol, timeframe, drawings, include_ai,
                                  question, position)

        if not question:
            await cache.set_json(key, result.model_dump(mode="json"), CACHE_TTL_SECONDS)

        # Attached after caching on purpose: account risk moves with every
        # close anywhere in the platform, so a 20-second-old copy of it would
        # be stale in exactly the situation it exists to catch.
        result.account_risk = await _account_risk(session)

        try:
            await journal_service.record_analysis(session, result)
        except Exception as exc:  # noqa: BLE001 - audit log must never block
            log.debug("failed to record analysis: %s", exc)

        return result


@router.post("/run", response_model=AnalysisResult)
async def run_analysis(
    payload: AnalysisRequest,
    session: AsyncSession = Depends(get_session),
) -> AnalysisResult:
    """Full analysis of a symbol/timeframe including the trader's drawings."""
    return await _run(payload.symbol, payload.timeframe, session,
                      payload.include_ai, payload.force_refresh, payload.question)


@router.get("/run", response_model=AnalysisResult)
async def run_analysis_get(
    symbol: str = Query(...),
    timeframe: str = Query("15m"),
    include_ai: bool = Query(True),
    force: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> AnalysisResult:
    return await _run(symbol, timeframe, session, include_ai, force)


@router.post("/ask", response_model=AnalysisResult)
async def ask_question(
    payload: AnalysisRequest,
    session: AsyncSession = Depends(get_session),
) -> AnalysisResult:
    """Ask the analyst a specific question about the current chart."""
    if not payload.question:
        raise HTTPException(status_code=422, detail="question is required")
    return await _run(payload.symbol, payload.timeframe, session, True, True,
                      payload.question)


@router.get("/watchlist")
async def watchlist(
    timeframe: str = Query("1h"),
    symbols: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fast directional read across instruments — no AI, no patterns."""
    wanted = ([s.strip().upper() for s in symbols.split(",")]
          if symbols else DEFAULT_STREAM_SYMBOLS)
    results = await asyncio.gather(
        *(engine.quick_bias(symbol, timeframe) for symbol in wanted),
        return_exceptions=True,
    )
    out: List[Dict[str, Any]] = []
    for symbol, item in zip(wanted, results):
        if isinstance(item, Exception):
            out.append({"symbol": symbol, "timeframe": timeframe,
                        "bias": "neutral", "score": 0.0, "note": str(item)})
        else:
            out.append(item)
    return out


@router.get("/ai-status")
async def ai_status() -> Dict[str, Any]:
    status = claude_client.availability()
    status["auto_interval_seconds"] = settings.ai_auto_analysis_interval
    status["fallback"] = "deterministic confluence engine"
    return status


async def run_for_stream(symbol: str, timeframe: str, include_ai: bool = True,
                         force: bool = False) -> Optional[AnalysisResult]:
    """Entry point used by the WebSocket auto-analysis loop."""
    try:
        async with session_scope() as session:
            return await _run(symbol, timeframe, session, include_ai, force)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("stream analysis failed for %s %s: %s", symbol, timeframe, exc)
        return None
