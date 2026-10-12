"""The one flag that decides whether an order can ever reach a real broker.

Deliberately not in `runtime_settings.py`: that store persists to disk and
reloads on every restart, and §33 of the live-trading spec requires that a
restart can never silently resume LIVE — a backend crash mid-session should
come back in PAPER and force a human to re-arm it, not quietly keep firing
real orders into whatever state the process was in when it died. So the mode
is in-memory only, seeded from the `TRADING_MODE` env var at boot, and
switching to LIVE always requires an explicit, freshly-made API call with
confirmation.

The kill switch lives here too, because it needs to be checked by every
single order-placing code path with zero chance of being accidentally
bypassed by a stale cached value. It is the opposite kind of state from the
mode, though, and is kept the opposite way: **the mode forgets, the kill
switch remembers.** Once tripped it stays tripped — through a switch to PAPER
and back, and through a restart — until someone explicitly clears it. An
emergency stop that a mode toggle or a process bounce quietly undoes is not
one, least of all when what tripped it was a loss limit.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from ..config import DATA_DIR, get_settings

log = logging.getLogger("meridian.trading_mode")

PAPER = "PAPER"
LIVE = "LIVE"

KILL_SWITCH_PATH = DATA_DIR / "kill_switch.json"


class TradingModeStore:
    def __init__(self, state_path: Optional[Path] = None) -> None:
        """`state_path` is where the kill switch is remembered. Without one
        the store is purely in-memory, which is what a unit test wants and
        what the process-wide instance below must never be."""
        self._lock = threading.RLock()
        self._state_path = state_path
        seed = get_settings().trading_mode
        self._mode = LIVE if seed == LIVE else PAPER
        self._live_confirmed_at: Optional[float] = None
        self._kill_switch = False
        self._kill_reason: Optional[str] = None
        self._kill_at: Optional[float] = None
        #: Whether a confirmed signal is allowed to place a real order without
        #: a human clicking Execute. Same restart policy as `mode` itself —
        #: in-memory only, never seeded true, so a crash can never come back
        #: silently placing unattended real orders. Every order it places
        #: still goes through the exact same `PreTradeRiskGate` a manual
        #: click would (edge requirement, daily loss, funds, the MCX/CDS
        #: blocks — all of it); this flag only decides who clicks Execute.
        self._auto_live = False
        self._load_kill_switch()

    # ── kill switch persistence ───────────────────────────────

    def _load_kill_switch(self) -> None:
        path = self._state_path
        if path is None or not path.exists():
            return
        try:
            state = json.loads(path.read_text())
            active = bool(state.get("active"))
            reason, at = state.get("reason"), state.get("activated_at")
        except (ValueError, OSError, AttributeError):
            # A file that exists but cannot be read is not evidence the
            # switch is off. Fail towards "no live orders".
            log.exception("kill switch state unreadable — treating it as ACTIVE")
            active, reason, at = True, "Kill switch state file was unreadable.", time.time()
        self._kill_switch, self._kill_reason, self._kill_at = active, reason, at
        if active:
            log.warning("kill switch is ACTIVE from a previous session: %s",
                        reason or "no reason recorded")

    def _persist_kill_switch(self) -> None:
        path = self._state_path
        if path is None:
            return
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "active": self._kill_switch, "reason": self._kill_reason,
                "activated_at": self._kill_at,
            }))
            os.replace(tmp, path)
        except OSError:
            # Still in force for this process; only the restart guarantee is lost.
            log.exception("could not persist kill switch state")

    # ── state ─────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    @property
    def is_live(self) -> bool:
        return self.mode == LIVE

    @property
    def kill_switch_active(self) -> bool:
        with self._lock:
            return self._kill_switch

    @property
    def kill_switch_reason(self) -> Optional[str]:
        with self._lock:
            return self._kill_reason

    def can_trade_live(self) -> bool:
        """The single check every live-order code path must call first."""
        with self._lock:
            return self._mode == LIVE and not self._kill_switch

    @property
    def auto_live_enabled(self) -> bool:
        with self._lock:
            return self._auto_live

    def can_auto_trade_live(self) -> bool:
        """The single check the unattended path must call before it ever
        reaches for `AngelOneExecutionService.execute` — stricter than
        `can_trade_live` because a human isn't there to notice the flag is
        stale."""
        with self._lock:
            return self._mode == LIVE and not self._kill_switch and self._auto_live

    def set_mode(self, mode: str, confirmed: bool = False) -> None:
        mode = mode.upper()
        if mode not in (PAPER, LIVE):
            raise ValueError("mode must be PAPER or LIVE")
        if mode == LIVE and not confirmed:
            raise PermissionError(
                "Switching to LIVE requires explicit confirmation "
                "(confirmed=true) — this is not optional.")
        with self._lock:
            self._mode = mode
            self._live_confirmed_at = time.time() if mode == LIVE else None
            if mode != LIVE:
                # Leaving LIVE always drops auto-live too — it must never be
                # the thing still armed when someone thinks they stepped back
                # to PAPER.
                self._auto_live = False
            # The kill switch is deliberately untouched here: see the module
            # docstring. Toggling the mode is not a way to clear it.

    def set_auto_live(self, enabled: bool, confirmed: bool = False) -> None:
        """Arm or disarm unattended live execution. Arming it needs the same
        explicit confirmation as the LIVE switch itself, for the same reason:
        this is the one setting that turns a signal into an order with nobody
        watching."""
        if enabled and not confirmed:
            raise PermissionError(
                "Enabling automatic live trading requires explicit "
                "confirmation (confirmed=true) — this is not optional.")
        with self._lock:
            if enabled and self._mode != LIVE:
                raise PermissionError(
                    "Switch trading mode to LIVE before enabling automatic "
                    "live trading.")
            if enabled and self._kill_switch:
                raise PermissionError(
                    "The kill switch is active — clear it before enabling "
                    "automatic live trading.")
            self._auto_live = enabled
        log.warning("auto-live trading %s", "ENABLED" if enabled else "disabled")

    def activate_kill_switch(self, reason: Optional[str] = None) -> None:
        with self._lock:
            if not self._kill_switch:
                self._kill_at = time.time()
            self._kill_switch = True
            self._kill_reason = reason or self._kill_reason or "Activated manually."
            # The one thing that must never survive an emergency stop.
            self._auto_live = False
            self._persist_kill_switch()
        log.warning("KILL SWITCH ACTIVE — %s", self._kill_reason)

    def deactivate_kill_switch(self) -> None:
        with self._lock:
            was_active = self._kill_switch
            self._kill_switch, self._kill_reason, self._kill_at = False, None, None
            self._persist_kill_switch()
        if was_active:
            log.warning("kill switch cleared")

    def status(self) -> dict:
        with self._lock:
            return {
                "mode": self._mode,
                "kill_switch_active": self._kill_switch,
                "kill_switch_reason": self._kill_reason,
                "kill_switch_activated_at": self._kill_at,
                "live_confirmed_at": self._live_confirmed_at,
                "auto_live_enabled": self._auto_live,
            }


trading_mode = TradingModeStore(state_path=KILL_SWITCH_PATH)
