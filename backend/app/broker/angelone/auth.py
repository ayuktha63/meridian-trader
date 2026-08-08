"""Angel One SmartAPI authentication.

Deliberately separate from AngelOneBrokerService: everything that touches a
credential lives here, and nothing outside this module ever sees the
password, TOTP secret, or the session tokens SmartAPI hands back. The rest of
the broker layer asks for an authenticated `SmartConnect` client and gets one
or an exception — it never learns why.

A session is good for a trading day; SmartAPI expires it, not us. `ensure()`
transparently re-logs-in when that happens instead of leaving the broker
layer to rediscover expiry via a failed order — which, for a live order, is
the wrong moment to find out.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import pyotp
from SmartApi import SmartConnect

from ...config import get_settings

log = logging.getLogger("meridian.angelone.auth")

#: SmartAPI sessions are valid for the trading day; re-login well before any
#: plausible server-side expiry rather than reacting to a 403 mid-order.
SESSION_MAX_AGE_SECONDS = 6 * 3600


@dataclass
class AngelSession:
    client: SmartConnect
    client_code: str
    feed_token: str
    refresh_token: str
    profile: dict
    established_at: float

    @property
    def age_seconds(self) -> float:
        return time.time() - self.established_at

    @property
    def stale(self) -> bool:
        return self.age_seconds > SESSION_MAX_AGE_SECONDS


class AngelOneAuthError(RuntimeError):
    """Login failed or credentials are missing. Never carries the raw secret."""


class AngelOneAuthService:
    """Owns the SmartAPI session. One account, one session, thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session: Optional[AngelSession] = None
        self._last_error: Optional[str] = None

    @property
    def configured(self) -> bool:
        return get_settings().angel_configured

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._session is not None and not self._session.stale

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def session(self) -> Optional[AngelSession]:
        with self._lock:
            return self._session

    def status(self) -> dict:
        with self._lock:
            return {
                "configured": self.configured,
                "connected": self.connected,
                "session_age_seconds": (
                    round(self._session.age_seconds, 0) if self._session else None
                ),
                "last_error": self._last_error,
            }

    def login(self) -> AngelSession:
        """Log in fresh, replacing any existing session. Raises on failure."""
        cfg = get_settings()
        if not cfg.angel_configured:
            raise AngelOneAuthError(
                "Angel One is not configured — set ANGEL_API_KEY, "
                "ANGEL_CLIENT_CODE, ANGEL_PASSWORD and ANGEL_TOTP_SECRET.")

        with self._lock:
            try:
                totp = pyotp.TOTP(cfg.angel_totp_secret).now()
                client = SmartConnect(api_key=cfg.angel_api_key)
                data = client.generateSession(
                    cfg.angel_client_code, cfg.angel_password, totp)
            except Exception as exc:  # noqa: BLE001 - normalise every SDK failure
                self._last_error = "login failed"
                self._session = None
                log.warning("Angel One login failed: %s", type(exc).__name__)
                raise AngelOneAuthError("Angel One login failed") from exc

            if not data or not data.get("status"):
                self._last_error = (data or {}).get("message", "login rejected")
                self._session = None
                log.warning("Angel One login rejected: %s", self._last_error)
                raise AngelOneAuthError(
                    "Angel One rejected the login: {}".format(self._last_error))

            profile = data.get("data") or {}
            self._session = AngelSession(
                client=client, client_code=cfg.angel_client_code,
                feed_token=profile.get("feedToken", ""),
                refresh_token=profile.get("refreshToken", ""),
                profile=profile, established_at=time.time())
            self._last_error = None
            log.info("Angel One session established for %s", cfg.angel_client_code)
            return self._session

    def ensure(self) -> SmartConnect:
        """The authenticated client, logging in or refreshing as needed."""
        with self._lock:
            if self._session is not None and not self._session.stale:
                return self._session.client
        return self.login().client

    def logout(self) -> None:
        with self._lock:
            if self._session is not None:
                try:
                    self._session.client.terminateSession(self._session.client_code)
                except Exception:  # noqa: BLE001 - best effort, we're dropping it either way
                    pass
            self._session = None
            log.info("Angel One session terminated")


#: Process-wide singleton — one broker session per backend instance, mirroring
#: how the market data feed and settings store are already module-level.
angel_auth = AngelOneAuthService()
