"""What an operator needs to see from outside the process: logs a machine
can read, a readiness answer, and numbers to graph.

No dependency is added for any of it. The metrics endpoint speaks the
Prometheus text format, which is a few lines of string formatting, and it
sits behind the same access rule as every other route — this machine only,
unless an access token is set.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Tuple

from sqlalchemy import func, select

from . import alerts
from .broker.angelone.auth import angel_auth, redact_secrets
from .broker.trading_mode import trading_mode
from .data.feed import SYNTHETIC, feed
from .data.store import store
from .db.models import ExperimentRow, LiveExecutionRow
from .db.session import session_scope

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, and whatever
    was passed as `extra`. Secrets are redacted on the way out, as they are
    in the SDK's own logs."""

    def format(self, record: logging.LogRecord) -> str:
        entry: Dict[str, Any] = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                    + ".{:03d}Z".format(int(record.msecs)),
            "level": record.levelname, "logger": record.name,
            "message": redact_secrets(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exception"] = redact_secrets(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


def configure_logging(level: str, fmt: str) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler()
    if fmt.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-7s %(name)s  %(message)s", "%H:%M:%S"))
    root.handlers[:] = [handler]
    for noisy in ("httpx", "httpcore", "websockets.client", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── freshness ─────────────────────────────────────────────────

def feed_freshness(now: float = 0.0) -> Dict[str, Any]:
    """How old the newest quote is for each symbol that has one, and which
    loaded series are simulated stand-ins."""
    now = now or time.time()
    ages = {symbol: round(now - quote.updated_at, 1) for symbol, quote in feed.quotes.items()}
    sources: Dict[str, int] = {}
    stand_ins: List[str] = []
    for symbol, timeframe in store.keys():
        series = store.series(symbol, timeframe)
        if not len(series):
            continue
        source = series.source or "unknown"
        sources[source] = sources.get(source, 0) + 1
        if source == SYNTHETIC and feed.provider_for(symbol) != SYNTHETIC:
            stand_ins.append("{}:{}".format(symbol, timeframe))
    return {"quote_age_seconds": ages, "oldest_quote_seconds": max(ages.values(), default=None),
            "series_by_source": sources, "simulated_stand_ins": sorted(stand_ins)}


# ── readiness ─────────────────────────────────────────────────

async def readiness() -> Tuple[bool, Dict[str, Any]]:
    """Whether this process can do its job right now — not merely whether
    it is running. Live trading raises the bar: the broker has to be
    connected and the supervisor alive."""
    from .broker.angelone.reconciler import reconciler

    checks: Dict[str, Any] = {}
    try:
        async with session_scope() as session:
            await session.execute(select(1))
        checks["database"] = True
    except Exception as exc:  # noqa: BLE001 - any failure here is "not ready"
        checks["database"] = False
        checks["database_error"] = str(exc)
    status = feed.status()
    checks["feed"] = bool(status.get("running"))
    checks["mode"] = trading_mode.mode
    if trading_mode.is_live:
        checks["broker_connected"] = bool(angel_auth.connected)
        task = getattr(reconciler, "_task", None)
        checks["supervisor_running"] = task is not None and not task.done()
    ready = all(value for key, value in checks.items()
                if isinstance(value, bool))
    return ready, checks


# ── metrics ───────────────────────────────────────────────────

def _line(name: str, value: Any, labels: Dict[str, str] = None, help_text: str = "",
          kind: str = "gauge", seen: set = None) -> List[str]:
    out: List[str] = []
    if seen is not None and name not in seen:
        seen.add(name)
        if help_text:
            out.append("# HELP {} {}".format(name, help_text))
        out.append("# TYPE {} {}".format(name, kind))
    rendered = ""
    if labels:
        rendered = "{" + ",".join('{}="{}"'.format(k, str(v).replace('"', "'"))
                                  for k, v in sorted(labels.items())) + "}"
    out.append("{}{} {}".format(name, rendered, float(value)))
    return out


async def metrics_text(started_at: float) -> str:
    seen: set = set()
    lines: List[str] = []

    def add(name: str, value: Any, help_text: str = "", **labels: str) -> None:
        lines.extend(_line("meridian_" + name, value, labels or None, help_text, seen=seen))

    now = time.time()
    add("uptime_seconds", now - started_at, "Seconds since the process started.")
    add("live_mode", 1 if trading_mode.is_live else 0, "1 when LIVE mode is armed.")
    add("kill_switch", 1 if trading_mode.kill_switch_active else 0,
        "1 while the kill switch is on.")
    add("broker_connected", 1 if angel_auth.connected else 0,
        "1 when an Angel One session exists.")
    status = feed.status()
    add("feed_running", 1 if status.get("running") else 0, "1 when the market feed is up.")
    add("feed_subscribers", status.get("subscribers", 0), "Open stream connections.")
    fresh = feed_freshness(now)
    for source, count in sorted(fresh["series_by_source"].items()):
        add("candle_series", count, "Loaded candle series, by where the bars came from.",
            source=source)
    add("simulated_stand_ins", len(fresh["simulated_stand_ins"]),
        "Series showing simulated bars because their venue did not answer.")
    for symbol, age in sorted(fresh["quote_age_seconds"].items()):
        add("quote_age_seconds", age, "Age of the newest quote.", symbol=symbol)

    counts: Dict[str, int] = {}
    for alert in alerts.recent():
        counts[alert["level"]] = counts.get(alert["level"], 0) + 1
    for level in ("info", "warning", "critical"):
        add("alerts_recent", counts.get(level, 0), "Alerts in the in-memory history.",
            level=level)

    try:
        async with session_scope() as session:
            rows = (await session.execute(
                select(LiveExecutionRow.status, func.count())
                .group_by(LiveExecutionRow.status))).all()
            for state, count in rows:
                add("live_executions", count, "Live executions on record, by status.",
                    status=state)
            experiments = (await session.execute(
                select(ExperimentRow.kind, func.count()).group_by(ExperimentRow.kind))).all()
            for kind, count in experiments:
                add("research_experiments", count, "Research experiments on record.",
                    kind=kind)
        add("database_up", 1, "1 when the database answered.")
    except Exception:  # noqa: BLE001 - a metrics scrape must not raise
        add("database_up", 0, "1 when the database answered.")
    return "\n".join(lines) + "\n"
