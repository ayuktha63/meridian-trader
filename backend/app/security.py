"""Who is allowed to talk to an API that can place real orders.

This app was built to run on `localhost` for a single user, and nothing in
it assumes otherwise — no login, no session, no per-endpoint permission
model. `POST /api/trades/{id}/execute` doesn't care who's asking, so this
module is the only thing that does. It runs in one of two modes:

**Local-only (no `APP_ACCESS_TOKEN`).** The default. A request is served only
if it demonstrably comes from this machine, which is three separate facts,
because binding to loopback alone does not establish it:

* the TCP peer is a loopback address, and so is every hop a proxy says it
  forwarded for — a tunnel or `vite --host` otherwise turns "localhost only"
  into "anyone who can reach the proxy";
* the `Host` header names loopback — otherwise any web page can point a
  hostname it controls at 127.0.0.1 and talk to this API as if same-origin
  (DNS rebinding);
* a browser-supplied `Origin`, when there is one, is this app's own UI —
  otherwise any web page the trader visits can fire a blind cross-site POST
  at `localhost:8000` and flip the trading mode or clear the kill switch.

**Token (`APP_ACCESS_TOKEN` set).** Every request must carry it, wherever it
comes from. This is the only mode in which the server may bind a
non-loopback address: `assert_safe_bind` refuses to start otherwise.

Rather than build a real user/session system for a single-operator tool,
this is deliberately the simplest thing that closes the actual hole. If you
need more than one user or finer-grained permissions, this is not that
system — build a real one before you need it.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import re
from typing import Iterable, List, Optional
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

from .config import settings

#: Requests to these paths never need the token — the root/docs routes leak
#: nothing sensitive (no credentials, no trade data, no ability to act).
_EXEMPT_PATHS = {"/", "/docs", "/openapi.json", "/redoc"}

#: Origins the UI is served from in every supported setup: the Vite dev
#: server on whatever port it picked, a preview build, and the packaged
#: desktop app. Shared with the CORS layer in `main.py` so the two can never
#: disagree about what "our own frontend" means.
LOCAL_ORIGIN_REGEX = (
    r"^(http://localhost:\d+|http://127\.0\.0\.1:\d+|http://\[::1\]:\d+"
    r"|app://.*|file://.*)$"
)
_LOCAL_ORIGIN = re.compile(LOCAL_ORIGIN_REGEX)

#: Headers a proxy uses to say who it is forwarding for.
_FORWARD_HEADERS = (b"x-forwarded-for", b"x-real-ip", b"cf-connecting-ip",
                    b"true-client-ip")

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

LOCAL_ONLY_DETAIL = (
    "This API only answers requests from this machine until "
    "APP_ACCESS_TOKEN is set."
)


class InsecureBindError(RuntimeError):
    """Refusing to listen on the network with no access token."""


def _is_loopback_address(value: str) -> bool:
    value = value.strip().strip('"')
    if value.startswith("["):                 # [::1]:8000
        value = value[1:].split("]", 1)[0]
    elif value.count(":") == 1:               # 127.0.0.1:8000
        value = value.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


def is_loopback_host(host: Optional[str]) -> bool:
    """True for a bind address or `Host` value that can only mean this machine."""
    if not host:
        return False
    host = host.strip().lower()
    if host == "localhost" or host.startswith("localhost:"):
        return True
    return _is_loopback_address(host)


def assert_safe_bind(host: str, token: Optional[str]) -> None:
    """Raise unless listening on `host` is safe with this token setting."""
    if is_loopback_host(host) or (token and token.strip()):
        return
    raise InsecureBindError(
        "Refusing to listen on {!r} without APP_ACCESS_TOKEN.\n"
        "This API can place real orders and has no other authentication, so "
        "it only binds beyond loopback when a token is set.\n"
        "  - to run locally:   API_HOST=127.0.0.1 (the default)\n"
        "  - to expose it:     set APP_ACCESS_TOKEN to a long random value, "
        "e.g.  python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        .format(host))


def _forwarded_hops(headers: dict) -> List[str]:
    hops: List[str] = []
    for name in _FORWARD_HEADERS:
        raw = headers.get(name)
        if raw:
            hops += [part for part in raw.decode("latin-1").split(",") if part.strip()]
    forwarded = headers.get(b"forwarded")
    if forwarded:
        # RFC 7239: `for=192.0.2.60;proto=http, for="[2001:db8::1]:4711"`
        found = re.findall(r'for=("[^"]*"|[^;,\s]+)', forwarded.decode("latin-1"), re.I)
        # A Forwarded header we cannot read is still a proxy announcing itself.
        hops += found or ["unparseable"]
    return hops


def _origin_allowed(origin: str, configured: Iterable[str]) -> bool:
    return origin in configured or bool(_LOCAL_ORIGIN.match(origin))


def local_request_problem(scope: Scope) -> Optional[str]:
    """Why this request cannot be treated as coming from this machine's own
    UI — or None if it can. Only consulted when no access token is set."""
    headers = dict(scope.get("headers") or [])

    client = scope.get("client")
    if not client or not _is_loopback_address(str(client[0])):
        return "request did not come from this machine"

    if not all(_is_loopback_address(hop) for hop in _forwarded_hops(headers)):
        return "request was forwarded by a proxy for another machine"

    if not is_loopback_host(headers.get(b"host", b"").decode("latin-1")):
        return "request was addressed to a hostname that is not this machine"

    origin = headers.get(b"origin")
    if origin is not None:
        # Browsers attach Origin to every cross-site write and to every
        # websocket handshake. Reads are left alone: a cross-site GET cannot
        # show its response to the page that sent it (CORS), and plenty of
        # same-origin GETs carry no Origin at all.
        unsafe = (scope["type"] == "websocket"
                  or scope.get("method", "GET") not in _SAFE_METHODS)
        if unsafe and not _origin_allowed(origin.decode("latin-1"),
                                          settings.cors_origins):
            return "request came from a web page that is not this app"
    return None


def _extract_token(scope: Scope) -> Optional[str]:
    headers = dict(scope.get("headers") or [])
    auth = headers.get(b"authorization", b"").decode("latin-1")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    # WebSocket handshakes from a browser can't set custom headers, so the
    # token travels as a query param there instead — still only ever read
    # server-side, never logged.
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    values = query.get("token")
    return values[0] if values else None


def _token_matches(provided: Optional[str], expected: str) -> bool:
    if provided is None:
        return False
    # Constant-time: a plain `==` returns sooner the earlier the first wrong
    # byte is, which is enough to recover a secret one character at a time.
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


class AccessTokenMiddleware:
    """Raw ASGI middleware (not BaseHTTPMiddleware) because it has to guard
    the websocket scope too, not just HTTP."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        token = (settings.app_access_token or "").strip()

        if not token:
            problem = local_request_problem(scope)
            if problem is None:
                await self.app(scope, receive, send)
            else:
                await self._refuse(scope, send, 403, 4403,
                                   "{} ({}).".format(LOCAL_ONLY_DETAIL.rstrip("."), problem))
            return

        # CORS preflight carries no custom headers by definition.
        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if scope["path"] in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        if _token_matches(_extract_token(scope), token):
            await self.app(scope, receive, send)
            return

        await self._refuse(scope, send, 401, 4401, "Missing or invalid access token.")

    @staticmethod
    async def _refuse(scope: Scope, send: Send, status: int, ws_code: int,
                      detail: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": ws_code})
            return
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send({
            "type": "http.response.start", "status": status,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": body})
