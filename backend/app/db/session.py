"""Async engine + session factory with automatic SQLite fallback."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ..config import settings
from .models import Base

log = logging.getLogger("meridian.db")

_engine: Optional[AsyncEngine] = None
_factory: Optional[async_sessionmaker] = None
_dialect: str = "unset"


def _build_engine(url: str) -> AsyncEngine:
    kwargs = {"echo": False, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        # No pooling for SQLite: every session opens its own connection and
        # closes it. An aiosqlite connection is a thread that answers to the
        # event loop it was opened under; kept in a pool, it gets handed to
        # code running under a different loop — every test helper that calls
        # asyncio.run does that — and a close awaited there can wait for
        # ever. Opening a SQLite file costs microseconds, so there is nothing
        # to pool for.
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
        kwargs["pool_pre_ping"] = True
    return create_async_engine(url, **kwargs)


async def init_db() -> str:
    """Create the engine and schema. Falls back to SQLite if Postgres is down."""
    global _engine, _factory, _dialect

    url = settings.resolved_database_url
    try:
        engine = _build_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _dialect = "postgres" if "postgres" in url else "sqlite"
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the app
        if url.startswith("sqlite"):
            raise
        log.warning("Postgres unavailable (%s); falling back to SQLite", exc)
        from ..config import DATA_DIR

        url = "sqlite+aiosqlite:///" + str(DATA_DIR / "meridian.db")
        engine = _build_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _dialect = "sqlite"

    _engine = engine
    _factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await _add_missing_columns(engine)
    log.info("database ready (%s)", _dialect)
    return _dialect


#: Columns added after the first release, as (table, column, DDL type, default).
#: `create_all` only creates missing *tables*, so a database made by an earlier
#: version keeps its old columns forever and every query against a new one
#: fails. This is a deliberately minimal migration step — enough for additive
#: columns, which is all this schema has needed. Anything destructive should
#: get a real migration tool.
_ADDED_COLUMNS = [
    ("positions", "initial_stop", "FLOAT", "0"),
    ("positions", "protection_tier", "INTEGER", "0"),
    ("positions", "peak_r", "FLOAT", "0"),
    ("positions", "trough_r", "FLOAT", "0"),
    ("positions", "broker_stop", "FLOAT", "NULL"),
    ("paper_accounts", "timeframes", "TEXT", "NULL"),
    ("live_executions", "order_tag", "VARCHAR(20)", "NULL"),
    ("live_executions", "tick_size", "FLOAT", "0.05"),
    ("live_executions", "initial_stop", "FLOAT", "NULL"),
    ("live_executions", "protection_tier", "INTEGER", "0"),
    ("live_executions", "protection_status", "VARCHAR(16)", "'NONE'"),
    ("live_executions", "stop_order_id", "VARCHAR(32)", "NULL"),
    ("live_executions", "target_order_id", "VARCHAR(32)", "NULL"),
    ("live_executions", "protected_quantity", "FLOAT", "0"),
    ("live_executions", "exit_order_id", "VARCHAR(32)", "NULL"),
    ("live_executions", "exit_price", "FLOAT", "NULL"),
    ("live_executions", "exit_reason", "VARCHAR(24)", "NULL"),
    ("live_executions", "gross_pnl", "FLOAT", "NULL"),
    ("live_executions", "closed_at", "FLOAT", "NULL"),
    ("live_executions", "timeframe", "VARCHAR(8)", "NULL"),
    ("live_executions", "submitted_at", "FLOAT", "NULL"),
    ("live_executions", "filled_at", "FLOAT", "NULL"),
    ("live_executions", "child_order_ids", "JSON", "'[]'"),
    ("live_executions", "exit_quantity", "FLOAT", "0"),
]


async def _add_missing_columns(engine) -> None:
    from sqlalchemy import inspect as sa_inspect, text

    async with engine.begin() as conn:
        tables = await conn.run_sync(lambda sync: sa_inspect(sync).get_table_names())
        for table, column, ddl_type, default in _ADDED_COLUMNS:
            if table not in tables:
                continue
            existing = await conn.run_sync(
                lambda sync, t=table: {c["name"] for c in sa_inspect(sync).get_columns(t)})
            if column in existing:
                continue
            await conn.execute(text(
                "ALTER TABLE {} ADD COLUMN {} {} DEFAULT {}".format(
                    table, column, ddl_type, default)))
            log.info("migrated: added %s.%s", table, column)

        # Positions written before `initial_stop` existed have it at 0, which
        # would read as an infinite risk unit. Their current stop is the best
        # available estimate of where it started.
        if "positions" in tables:
            await conn.execute(text(
                "UPDATE positions SET initial_stop = stop_loss "
                "WHERE initial_stop IS NULL OR initial_stop = 0"))


async def shutdown_db() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def dialect() -> str:
    return _dialect


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    if _factory is None:
        await init_db()
    assert _factory is not None
    async with _factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with session_scope() as session:
        yield session
