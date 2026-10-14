"""Peak-to-trough drawdown on the live account — the gap the audit named
explicitly: "Daily loss is bound; a week of small losses is not."

The daily-loss check in `live_limits.py` resets every midnight IST — a
losing *day* trips it, a losing *month* made of small, individually-fine
days never does. This tracks the account's own high-water mark across every
day it has ever traded, not just today, and locks new entries once the
drawdown from that peak passes a hard line.

Equity here is defined as: Angel One's own reported available cash, plus
the mark-to-market of every currently open live position. That's the same
two numbers the rest of the live path already trusts (`ctx.available_funds`,
`LiveLedger`'s open rows) — this adds no new source of truth, only a
memory of the best the first one has ever been.

Kept the same way the kill switch is, and for the same reason: **the mode
forgets, this remembers.** A restart must not quietly erase how deep the
account had fallen — that would hand back exactly the "a crash resets my
risk" loophole the kill switch exists to close for the daily case.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import DATA_DIR, get_settings

log = logging.getLogger("meridian.drawdown")

STATE_PATH = DATA_DIR / "drawdown.json"


@dataclass
class DrawdownStatus:
    peak_equity: Optional[float]
    last_equity: Optional[float]
    drawdown: float               # currency, >= 0
    drawdown_pct: float           # of the peak, >= 0
    locked: bool
    locked_reason: Optional[str]
    locked_at: Optional[float]
    updated_at: Optional[float]


class DrawdownTracker:
    """`state_path=None` is in-memory only — what a unit test wants, and
    never what the process-wide instance below may be."""

    def __init__(self, state_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._state_path = state_path
        self._peak: Optional[float] = None
        self._last: Optional[float] = None
        self._locked = False
        self._locked_reason: Optional[str] = None
        self._locked_at: Optional[float] = None
        self._updated_at: Optional[float] = None
        self._load()

    # ── persistence ───────────────────────────────────────────

    def _load(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
            self._peak = data.get("peak_equity")
            self._last = data.get("last_equity")
            self._locked = bool(data.get("locked", False))
            self._locked_reason = data.get("locked_reason")
            self._locked_at = data.get("locked_at")
            self._updated_at = data.get("updated_at")
        except (OSError, ValueError, TypeError) as exc:
            # Unreadable state is treated the same way the kill switch
            # treats it: towards no trading, not towards a clean slate.
            log.warning("drawdown state unreadable (%s) — locking until a human "
                        "looks at it.", exc)
            self._locked = True
            self._locked_reason = "Saved drawdown state could not be read ({}).".format(exc)
            self._locked_at = time.time()

    def _persist(self) -> None:
        if self._state_path is None:
            return
        try:
            scratch = self._state_path.with_suffix(".tmp")
            scratch.write_text(json.dumps({
                "peak_equity": self._peak, "last_equity": self._last,
                "locked": self._locked, "locked_reason": self._locked_reason,
                "locked_at": self._locked_at, "updated_at": self._updated_at,
            }))
            os.replace(scratch, self._state_path)
        except OSError as exc:
            log.warning("could not persist drawdown state: %s", exc)

    # ── the one thing callers do ──────────────────────────────

    def update(self, equity: float) -> Optional[str]:
        """Record this account-equity reading and, if it just breached the
        hard drawdown limit, lock and return why. None otherwise — already
        locked, or still within bounds. Every call updates the peak; only a
        fresh breach returns a reason, so a caller that also wants to trip
        the kill switch does it once, not on every subsequent check."""
        with self._lock:
            self._last = equity
            self._updated_at = time.time()
            if self._peak is None or equity > self._peak:
                self._peak = equity
                self._persist()
                return None
            if self._locked:
                self._persist()
                return None
            limit_pct = get_settings().live_max_drawdown_pct
            if not limit_pct or self._peak <= 0:
                self._persist()
                return None
            drawdown_pct = (self._peak - equity) / self._peak * 100.0
            if drawdown_pct < limit_pct:
                self._persist()
                return None
            reason = ("Account equity {:.2f} is {:.1f}% below its peak of {:.2f} "
                      "— past the {:.1f}% drawdown limit.".format(
                          equity, drawdown_pct, self._peak, limit_pct))
            self._locked = True
            self._locked_reason = reason
            self._locked_at = time.time()
            self._persist()
            log.warning("drawdown lock tripped: %s", reason)
            return reason

    def status(self) -> DrawdownStatus:
        with self._lock:
            drawdown = max((self._peak or 0.0) - (self._last or self._peak or 0.0), 0.0)
            drawdown_pct = (drawdown / self._peak * 100.0) if self._peak else 0.0
            return DrawdownStatus(
                peak_equity=self._peak, last_equity=self._last,
                drawdown=round(drawdown, 2), drawdown_pct=round(drawdown_pct, 2),
                locked=self._locked, locked_reason=self._locked_reason,
                locked_at=self._locked_at, updated_at=self._updated_at)

    def clear(self) -> None:
        """Explicit recovery — a human decided the account is being watched
        again, not a restart deciding it for them. Does not reset the peak:
        the high-water mark is a fact about the account's history, and
        clearing the lock is not supposed to also erase the reason it
        existed."""
        with self._lock:
            self._locked = False
            self._locked_reason = None
            self._locked_at = None
            self._persist()
            log.info("drawdown lock cleared by hand")


drawdown_tracker = DrawdownTracker(STATE_PATH)
