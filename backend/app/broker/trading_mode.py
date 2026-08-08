"""The one flag that decides whether an order can ever reach a real broker.

Deliberately not in `runtime_settings.py`: that store persists to disk and
reloads on every restart, and §33 of the live-trading spec requires that a
restart can never silently resume LIVE — a backend crash mid-session should
come back in PAPER and force a human to re-arm it, not quietly keep firing
real orders into whatever state the process was in when it died. So this is
in-memory only, seeded from the `TRADING_MODE` env var at boot, and switching
to LIVE always requires an explicit, freshly-made API call with confirmation.

The kill switch lives here too, for the same reason: it needs to be checked
by every single order-placing code path with zero chance of being
accidentally bypassed by a stale cached value.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from ..config import get_settings

PAPER = "PAPER"
LIVE = "LIVE"


class TradingModeStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        seed = get_settings().trading_mode
        self._mode = LIVE if seed == LIVE else PAPER
        # LIVE from the environment still is not allowed to arm the kill
        # switch state automatically — it only sets the *default* mode the
        # UI shows; §3/§8 still require the confirmation dialog before the
        # first live order.
        self._kill_switch = False
        self._live_confirmed_at: Optional[float] = None

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

    def can_trade_live(self) -> bool:
        """The single check every live-order code path must call first."""
        with self._lock:
            return self._mode == LIVE and not self._kill_switch

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
            if mode == PAPER:
                # Leaving LIVE always clears the kill switch too — nothing
                # about re-entering PAPER should carry stale emergency state.
                self._kill_switch = False

    def activate_kill_switch(self) -> None:
        with self._lock:
            self._kill_switch = True

    def deactivate_kill_switch(self) -> None:
        with self._lock:
            self._kill_switch = False

    def status(self) -> dict:
        with self._lock:
            return {
                "mode": self._mode,
                "kill_switch_active": self._kill_switch,
                "live_confirmed_at": self._live_confirmed_at,
            }


trading_mode = TradingModeStore()
