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
from fastapi.responses import ORJSONResponse

from . import __version__, runtime_settings
from .ai import claude_client
from .cache import cache
from .config import settings
from .data.feed import feed
from .db.session import dialect, init_db, shutdown_db
from .instruments import SYMBOLS, TIMEFRAME_CODES
from .routers import (
    analysis, backtest, broker_angel, drawings, execution, journal, market,
    paper, positions, settings as settings_router, ws,
)
from .security import AccessTokenMiddleware

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
for noisy in ("httpx", "httpcore", "websockets.client", "asyncio"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("meridian")

STARTED_AT = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Meridian %s starting", __version__)

    db_dialect = await init_db()
    cache_backend = await cache.connect()
    await feed.start()

    cfg = runtime_settings.get()
    log.info("ready — db=%s cache=%s provider=%s analyst=%s",
             db_dialect, cache_backend, cfg.market_provider,
             "claude:" + cfg.claude_model if runtime_settings.store.claude_enabled
             else "rule-engine")
    if not runtime_settings.store.claude_enabled:
        log.warning("No Anthropic API key — running the deterministic analyst. "
                    "Add one in Settings (gear icon) or in .env.")

    try:
        yield
    finally:
        log.info("shutting down")
        await feed.stop()
        await cache.close()
        await shutdown_db()


app = FastAPI(
    title="Meridian — AI Forex Trading Analysis",
    description=(
        "Institutional-grade market analysis. Produces BUY / SELL / WAIT "
        "recommendations with confidence, risk framing and full reasoning. "
        "**This service never executes trades.**"
    ),
    version=__version__,
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

# Runs inside CORS (added first here = inner layer) so it still sits ahead
# of every router and a single line of trading logic; CORS itself has to be
# outermost so its headers land on this middleware's 401s too — otherwise a
# rejected request shows the browser a CORS error instead of the real one.
# No-op locally: only activates once APP_ACCESS_TOKEN is set (security.py).
app.add_middleware(AccessTokenMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"^(http://localhost:\d+|http://127\.0\.0\.1:\d+|app://.*|file://.*)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market.router)
app.include_router(drawings.router)
app.include_router(analysis.router)
app.include_router(journal.router)
app.include_router(backtest.router)
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
            "Analysis only. This service does not place, modify or cancel orders. "
            "Nothing it outputs is a guarantee of any outcome."
        ),
    }


@app.get("/", tags=["system"])
async def root() -> Dict[str, str]:
    return {
        "name": "Meridian",
        "docs": "/docs",
        "health": "/api/health",
        "stream": "/ws/stream",
    }
