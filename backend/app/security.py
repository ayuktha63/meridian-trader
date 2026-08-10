"""The one thing standing between a deployed backend and anyone on the
internet placing a real order through it.

This app was built to run on `localhost` for a single user, and nothing in
it assumes otherwise — no login, no session, no per-endpoint permission
model. That's fine on your own machine. It stops being fine the moment the
backend is reachable from anywhere else, because `POST /api/trades/{id}/execute`
doesn't care who's asking.

Rather than build a real user/session system for a single-operator tool,
this is deliberately the simplest thing that closes the actual hole: one
shared secret, checked on every request, off by default so local dev is
unaffected. If you need more than one user or finer-grained permissions,
this is not that system — build a real one before you need it.
"""
from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from .config import settings

#: Requests to these paths never need the token — CORS preflight has no
#: custom headers by definition, and the root/docs routes leak nothing
#: sensitive (no credentials, no trade data, no ability to act).
_EXEMPT_PATHS = {"/", "/docs", "/openapi.json", "/redoc"}


def _extract_token(scope: Scope) -> str | None:
    headers = dict(scope.get("headers") or [])
    auth = headers.get(b"authorization", b"").decode("latin-1")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    # WebSocket handshakes from a browser can't set custom headers, so the
    # token travels as a query param there instead — still only ever read
    # server-side, never logged.
    query = scope.get("query_string", b"").decode("latin-1")
    for pair in query.split("&"):
        if pair.startswith("token="):
            return pair[len("token="):]
    return None


class AccessTokenMiddleware:
    """Raw ASGI middleware (not BaseHTTPMiddleware) because it has to guard
    the websocket scope too, not just HTTP."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = settings.app_access_token
        if not token or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "http" and scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if scope["path"] in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        provided = _extract_token(scope)
        if provided == token:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return

        await send({
            "type": "http.response.start", "status": 401,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"detail":"Missing or invalid access token."}',
        })
