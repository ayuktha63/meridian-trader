"""Meridian API.

Boots in a degraded-but-working state by design: if Postgres is down it falls
back to SQLite, if Redis is absent it uses an in-process cache, and if there is
no Anthropic key the deterministic analyst takes over. Nothing in the startup
path can prevent the platform from serving a chart.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, PlainTextResponse

from . import __version__, notify, observability, runtime_settings
from .ai import claude_client
from .cache import cache
from .config import settings
from .broker.angelone.reconciler import reconciler
from .data.feed import feed
from .db.session import dialect, init_db, shutdown_db
from .instruments import SYMBOLS, TIMEFRAME_CODES
from .routers import (
    analysis, backtest, broker_angel, drawings, execution, journal, market,
    paper, positions, research, settings as settings_router, ws,
)
from .security import LOCAL_ORIGIN_REGEX, AccessTokenMiddleware, assert_safe_bind

observability.configure_logging(settings.log_level, settings.log_format)

log = logging.getLogger("meridian")

STARTED_AT = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Meridian %s starting", __version__)
    # Raises, and so aborts startup, if API_HOST is a network address and no
    # access token is set. `python -m app.serve` has already said so more
    # politely; this catches every other way of launching the app.
    assert_safe_bind(settings.api_host, settings.app_access_token)

    db_dialect = await init_db()
    cache_backend = await cache.connect()
    await feed.start()
    # Picks up any live order left in flight by the last shutdown.
    await reconciler.start()
    if notify.install():
        log.info("alerts will also be sent to Telegram")

    cfg = runtime_settings.get()
    log.info("ready — db=%s cache=%s analyst=%s",
             db_dialect, cache_backend,
             "claude:" + cfg.claude_model if runtime_settings.store.claude_enabled
             else "rule-engine")
    if not runtime_settings.store.claude_enabled:
        log.warning("No Anthropic API key — running the deterministic analyst. "
                    "Add one in Settings (gear icon) or in .env.")

    try:
        yield
    finally:
        log.info("shutting down")
        await reconciler.stop()
        await feed.stop()
        await cache.close()
        await shutdown_db()


app = FastAPI(
    title="Meridian — Indian-market trading analysis (Angel One)",
    description=(
        "Market analysis for NSE and MCX instruments. Produces BUY / SELL / WAIT "
        "readings with a heuristic score, risk framing and full reasoning. "
        "Paper trading by default; it can place real orders through Angel "
        "One only in LIVE mode, after the pre-trade risk gate."
    ),
    version=__version__,
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

# Runs inside CORS (added first here = inner layer) so it still sits ahead
# of every router and a single line of trading logic; CORS itself has to be
# outermost so its headers land on this middleware's 401s too — otherwise a
# rejected request shows the browser a CORS error instead of the real one.
# With no APP_ACCESS_TOKEN it admits this machine only; with one, anyone who
# presents it (security.py).
app.add_middleware(AccessTokenMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=LOCAL_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router)
app.include_router(drawings.router)
app.include_router(analysis.router)
app.include_router(journal.router)
app.include_router(backtest.router)
app.include_router(research.router)
app.include_router(positions.router)
app.include_router(paper.router)
app.include_router(broker_angel.router)
app.include_router(execution.router)
app.include_router(settings_router.router)
app.include_router(ws.router)


@app.get("/api/health", tags=["system"])
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "version": __version__,
        "uptime_seconds": round(time.time() - STARTED_AT, 1),
        "database": dialect(),
        "cache": cache.backend,
        "analyst": claude_client.availability(),
        "feed": feed.status(),
        "symbols": SYMBOLS,
        "timeframes": TIMEFRAME_CODES,
        "disclaimer": (
            "PAPER mode by default: no real orders. In LIVE mode it does place "
            "orders on your Angel One account. Nothing it outputs is a "
            "guarantee of any outcome, and no setting makes trading risk-free."
        ),
    }


@app.get("/api/ready", tags=["system"])
async def ready() -> ORJSONResponse:
    """Whether this process can do its job right now: 200 or 503, with the
    reason. `/api/health` says the process is up; this says it is usable."""
    ok, checks = await observability.readiness()
    return ORJSONResponse({"ready": ok, "checks": checks,
                           "feed": observability.feed_freshness()},
                          status_code=200 if ok else 503)


@app.get("/metrics", tags=["system"], response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    """Prometheus text format. Behind the same access rule as everything
    else: this machine only, unless an access token is set."""
    return PlainTextResponse(await observability.metrics_text(STARTED_AT),
                             media_type="text/plain; version=0.0.4")


@app.get("/", tags=["system"])
async def root() -> Dict[str, str]:
    return {
        "name": "Meridian",
        "docs": "/docs",
        "health": "/api/health",
        "stream": "/ws/stream",
    }
