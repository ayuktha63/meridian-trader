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
import re
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import pyotp

from ...config import get_settings

if TYPE_CHECKING:  # pragma: no cover - typing only, see _load_sdk
    from SmartApi import SmartConnect

log = logging.getLogger("meridian.angelone.auth")

#: SmartAPI sessions are valid for the trading day; re-login well before any
#: plausible server-side expiry rather than reacting to a 403 mid-order.
SESSION_MAX_AGE_SECONDS = 6 * 3600

#: After a login fails, the next *automatic* one waits — this long, then
#: twice as long each time. The quote poll asks for a session every three
#: seconds, and with nothing here it logged in every three seconds too: 154
#: attempts in four minutes on 13 October 2026, while a DNS lookup was
#: failing. Against a wrong PIN that is how an account gets locked.
LOGIN_BACKOFF_BASE_SECONDS = 5.0
#: The broker could not be reached. That mends itself, and a position may be
#: waiting on it, so keep asking — every two minutes at the slowest.
LOGIN_BACKOFF_MAX_UNREACHABLE = 120.0
#: The broker answered and said no: a wrong PIN or TOTP, a revoked key. It
#: will say no again until somebody changes something, so ask rarely.
LOGIN_BACKOFF_MAX_REJECTED = 900.0
#: A session the broker disowns sooner than this after granting it was not
#: worth having, and counts as a failed login: otherwise "log in, be refused,
#: log in" is the same storm by another route.
MIN_SESSION_LIFE_SECONDS = 60.0

#: SmartAPI's published codes for a token that is invalid, expired or
#: missing, and for a session that has ended (the same ones
#: `data/angel_history.py` retries on).
AUTH_ERROR_CODES = frozenset({"AG8001", "AG8002", "AG8003", "AB1010", "AB1011"})


def is_auth_failure(response: object) -> bool:
    """Whether the broker's answer says the session is no longer one. The
    SDK returns these as an ordinary response, not an exception — on 12
    October 2026 the quote poll was told "Invalid Token" 559 times in half
    an hour and went on using the token."""
    if not isinstance(response, dict) or response.get("status"):
        return False
    code = str(response.get("errorcode") or response.get("errorCode") or "").upper()
    message = str(response.get("message") or "").lower()
    return code in AUTH_ERROR_CODES or "invalid token" in message or "token expired" in message


_sdk_lock = threading.Lock()
_smart_connect_cls = None


class _SkippedLookup:
    """Stands in for the response to the one request `_load_sdk` suppresses."""
    text = ""


def _load_sdk():
    """`SmartConnect`, imported on first use — never at app import.

    The SDK's class body makes a blocking GET to api.ipify.org with no timeout
    and then throws the answer away (a `finally` overwrites it with a
    hard-coded address). Imported at module level, that put a third-party
    network call on the boot path of a platform that promises to start with no
    network at all: on a network where that host stalls, `import app.main`
    took 150 seconds, and so did every test run.

    So the import is deferred to the first real login, and that one discarded
    lookup is answered locally. Every other `requests.get` passes straight
    through, so nothing else in the process can observe the swap.
    """
    global _smart_connect_cls
    with _sdk_lock:
        if _smart_connect_cls is None:
            import requests

            original_get = requests.get

            def _get(url, *args, **kwargs):
                if "api.ipify.org" in str(url):
                    return _SkippedLookup()
                return original_get(url, *args, **kwargs)

            requests.get = _get
            try:
                from SmartApi import SmartConnect
            finally:
                requests.get = original_get
            _redact_sdk_logs()
            _smart_connect_cls = SmartConnect
        return _smart_connect_cls


#: Request fields the SDK must never be allowed to write down. The last
#: three are not credentials: they are this machine's public address, local
#: address and network-card number, which the SDK sends in every request's
#: headers and — when a request fails — writes to the log with them.
_SECRET_KEYS = (
    "password", "totp", "authorization", "x-privatekey", "jwttoken",
    "refreshtoken", "feedtoken", "accesstoken", "access_token", "api_key",
    "clientcode",
    "x-clientpublicip", "x-clientlocalip", "x-macaddress",
)
#: A quoted string as repr() writes it, either quote style, escapes included.
_QUOTED = r"""(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")"""
#: `'key': 'value'` as it appears in the repr() of a dict.
_SECRET_FIELD = re.compile(
    r"""(['"](?:{})['"]\s*:\s*){}""".format(
        "|".join(re.escape(key) for key in _SECRET_KEYS), _QUOTED),
    re.IGNORECASE)
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}")


def redact_secrets(text: str) -> str:
    return _BEARER.sub("Bearer ***", _SECRET_FIELD.sub(r"\1'***'", text))


def mask_identifier(value: object) -> str:
    """An account identifier with all but its last two characters hidden —
    enough to tell two accounts apart in a log, not enough to name one."""
    text = str(value or "")
    return "*" * max(len(text) - 2, 0) + text[-2:] if len(text) > 2 else "**"


class _SecretRedactor(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg, record.args = redact_secrets(record.getMessage()), ()
        except Exception:  # noqa: BLE001 - a log line is never worth an exception
            record.msg, record.args = "[unprintable SmartApi log record]", ()
        return True


def _redact_sdk_logs() -> None:
    """Stop the SDK writing credentials to disk.

    On any failed request SmartApi logs the full request at ERROR — headers
    and body — to stderr and to `logs/<date>/app.log`. For a login that body
    is the account password and the TOTP; for everything after, the headers
    carry the session token and the API key. A wrong TOTP was enough to put
    the password in a plain-text file. The filter rewrites those records
    before any handler sees them; the rest of the line (URL, broker error) is
    left intact because it is what makes the failure diagnosable.
    """
    import logzero

    if not any(isinstance(f, _SecretRedactor) for f in logzero.logger.filters):
        logzero.logger.addFilter(_SecretRedactor())


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


class AngelOneLoginBackoff(AngelOneAuthError):
    """No session, and no login was attempted: the last one failed too
    recently. Nothing was sent to the broker."""


class AngelOneAuthService:
    """Owns the SmartAPI session. One account, one session, thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session: Optional[AngelSession] = None
        self._last_error: Optional[str] = None
        #: Attempts in a row that did not end in a session that lasted, and
        #: the time before which no automatic one is made. A login that
        #: succeeds does not clear the count by itself — a session has to
        #: outlive `MIN_SESSION_LIFE_SECONDS` first — or "log in, be
        #: disowned, log in" would start from nothing every time.
        self._failures = 0
        self._retry_at = 0.0

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
                "failed_logins": 0 if self.connected else self._failures,
                "login_retry_in_seconds": None if self.connected else self._retry_in(),
            }

    # ── backing off ───────────────────────────────────────────

    def _retry_in(self) -> Optional[float]:
        """Seconds until the next automatic login, or None if one may be
        made now."""
        left = self._retry_at - time.time()
        return round(left, 1) if left > 0 else None

    def _login_failed(self, rejected: bool) -> None:
        self._failures += 1
        ceiling = LOGIN_BACKOFF_MAX_REJECTED if rejected else LOGIN_BACKOFF_MAX_UNREACHABLE
        wait = min(LOGIN_BACKOFF_BASE_SECONDS * 2 ** (self._failures - 1), ceiling)
        self._retry_at = time.time() + wait
        log.warning("Angel One login has failed %s time(s) in a row; no automatic "
                    "attempt for %.0f s", self._failures, wait)

    def invalidate(self, reason: str) -> bool:
        """Drop a session the broker has said is no longer valid, so that the
        next `ensure` logs in again instead of going on presenting a dead
        token. True if there was one to drop."""
        with self._lock:
            session = self._session
            if session is None:
                return False
            self._session = None
            self._last_error = reason
            log.warning("Angel One session dropped after %.0f s: %s",
                        session.age_seconds, reason)
            if session.age_seconds < MIN_SESSION_LIFE_SECONDS:
                self._login_failed(rejected=True)
            else:
                self._failures = 0          # it lasted: the next login is a first try
            return True

    def login(self) -> AngelSession:
        """Log in fresh, replacing any existing session. Raises on failure.

        Always makes the attempt: this is what the Connect button calls, and
        a person asking is not a poll. A failure here still counts towards
        the wait the automatic path (`ensure`) observes."""
        cfg = get_settings()
        if not cfg.angel_configured:
            raise AngelOneAuthError(
                "Angel One is not configured — set ANGEL_API_KEY, "
                "ANGEL_CLIENT_CODE, ANGEL_PASSWORD and ANGEL_TOTP_SECRET.")

        with self._lock:
            try:
                totp = pyotp.TOTP(cfg.angel_totp_secret).now()
                client = _load_sdk()(api_key=cfg.angel_api_key)
                data = client.generateSession(
                    cfg.angel_client_code, cfg.angel_password, totp)
            except Exception as exc:  # noqa: BLE001 - normalise every SDK failure
                self._last_error = "login failed"
                self._session = None
                log.warning("Angel One login failed: %s", type(exc).__name__)
                self._login_failed(rejected=False)
                raise AngelOneAuthError("Angel One login failed") from exc

            if not data or not data.get("status"):
                self._last_error = (data or {}).get("message", "login rejected")
                self._session = None
                log.warning("Angel One login rejected: %s", self._last_error)
                self._login_failed(rejected=True)
                raise AngelOneAuthError(
                    "Angel One rejected the login: {}".format(self._last_error))

            profile = data.get("data") or {}
            self._session = AngelSession(
                client=client, client_code=cfg.angel_client_code,
                feed_token=profile.get("feedToken", ""),
                refresh_token=profile.get("refreshToken", ""),
                profile=profile, established_at=time.time())
            self._last_error = None
            self._retry_at = 0.0
            log.info("Angel One session established for %s",
                     mask_identifier(cfg.angel_client_code))
            return self._session

    def ensure(self) -> SmartConnect:
        """The authenticated client, logging in or refreshing as needed.

        This is the automatic path — every poll and every history request
        comes through it — so it is the one that waits after a failure. The
        check and the login are one step under the lock: two callers that
        find no session make one login between them, not one each."""
        with self._lock:
            if self._session is not None and not self._session.stale:
                if self._failures and self._session.age_seconds >= MIN_SESSION_LIFE_SECONDS:
                    self._failures = 0      # it lasted
                return self._session.client
            wait = self._retry_in()
            if wait is not None:
                raise AngelOneLoginBackoff(
                    "Angel One is not connected, and the last {} login attempt(s) failed "
                    "— the next automatic one is in {:.0f} s. Connect by hand to try "
                    "now.".format(self._failures, wait))
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
