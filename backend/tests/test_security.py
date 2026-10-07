"""Access control — who may talk to an API that can place real orders.

Two modes (see `app/security.py`): with no APP_ACCESS_TOKEN only this machine
is served; with one, every caller must present it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import serve
from app.config import settings
from app.main import app
from app.security import (
    InsecureBindError, _token_matches, assert_safe_bind, is_loopback_host,
)

REMOTE = ("203.0.113.9", 50000)


@pytest.fixture
def token(monkeypatch):
    """Set (or clear) the access token for one test."""
    def _set(value):
        monkeypatch.setattr(settings, "app_access_token", value)
    return _set


# ── local-only mode (no token) ────────────────────────────────

def test_no_token_serves_a_local_request(token):
    token(None)
    with TestClient(app) as client:
        assert client.get("/api/trading/mode").status_code == 200


def test_no_token_refuses_a_caller_on_another_machine(token):
    """The LAN case: bound to a reachable address with auth off, anyone on the
    network could switch modes or execute a trade."""
    token(None)
    with TestClient(app, client=REMOTE) as client:
        resp = client.get("/api/trading/mode")
        assert resp.status_code == 403
        assert "APP_ACCESS_TOKEN" in resp.json()["detail"]
        assert client.post("/api/trades/x/execute",
                           json={"confirmed": True}).status_code == 403
        assert client.post("/api/trading/kill-switch?activate=false").status_code == 403
        # Nothing is exempt for a stranger — not even the docs.
        assert client.get("/docs").status_code == 403


@pytest.mark.parametrize("header,value", [
    ("X-Forwarded-For", "203.0.113.9"),
    ("X-Forwarded-For", "127.0.0.1, 203.0.113.9"),
    ("X-Real-IP", "203.0.113.9"),
    ("Forwarded", "for=203.0.113.9;proto=https"),
    ("Forwarded", "by=something-unparseable"),
    ("X-Forwarded-For", "not-an-address"),
])
def test_no_token_refuses_a_request_proxied_for_another_machine(token, header, value):
    """A tunnel or reverse proxy on this machine connects from loopback. What
    it forwards is not local, and must not be treated as if it were."""
    token(None)
    with TestClient(app) as client:
        assert client.get("/api/trading/mode", headers={header: value}).status_code == 403


def test_no_token_accepts_a_proxy_forwarding_for_this_machine(token):
    """The Vite dev server proxies /api and reports the browser's address."""
    token(None)
    with TestClient(app) as client:
        for value in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            resp = client.get("/api/trading/mode", headers={"X-Forwarded-For": value})
            assert resp.status_code == 200, value


def test_no_token_refuses_a_foreign_host_header(token):
    """DNS rebinding: a page on evil.example re-points its own hostname at
    127.0.0.1, so the browser treats this API as same-origin with it."""
    token(None)
    with TestClient(app, base_url="http://evil.example:8000") as client:
        assert client.get("/api/trading/mode").status_code == 403


def test_no_token_refuses_a_cross_site_write(token):
    """A page the trader happens to visit can POST to localhost blind. The
    endpoints that take no body need nothing else to succeed."""
    token(None)
    with TestClient(app) as client:
        evil = {"Origin": "https://evil.example"}
        assert client.post("/api/trading/kill-switch?activate=false",
                           headers=evil).status_code == 403
        assert client.post("/api/trading/mode", headers=evil,
                           json={"mode": "LIVE", "confirmed": True}).status_code == 403
        assert client.post("/api/trading/kill-switch?activate=false",
                           headers={"Origin": "null"}).status_code == 403


def test_no_token_accepts_writes_from_the_apps_own_ui(token):
    token(None)
    with TestClient(app) as client:
        for origin in ("http://localhost:5173", "http://127.0.0.1:5174", "file://"):
            resp = client.post("/api/trading/mode", headers={"Origin": origin},
                               json={"mode": "PAPER"})
            assert resp.status_code == 200, origin


def test_no_token_refuses_a_cross_site_websocket(token):
    """Browsers do not apply CORS to websockets at all — the Origin check is
    the only thing between a foreign page and the stream."""
    token(None)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(
                    "/ws/stream", headers={"Origin": "https://evil.example"}):
                pass
        assert closed.value.code == 4403
        with client.websocket_connect(
                "/ws/stream", headers={"Origin": "http://localhost:5173"}) as socket:
            assert socket.receive_json()["type"] == "hello"


# ── token mode ────────────────────────────────────────────────

def test_configured_token_rejects_unauthenticated_requests(token):
    token("s3cret")
    with TestClient(app) as client:
        resp = client.get("/api/trading/mode")
        assert resp.status_code == 401


def test_configured_token_rejects_wrong_token(token):
    token("s3cret")
    with TestClient(app) as client:
        for wrong in ("wrong", "s3cre", "s3crets", "", "S3CRET"):
            resp = client.get("/api/trading/mode",
                              headers={"Authorization": "Bearer " + wrong})
            assert resp.status_code == 401, wrong


def test_token_comparison_handles_any_bytes_a_client_can_send():
    """`hmac.compare_digest` rejects non-ASCII `str` outright; a header is
    latin-1 on the wire, so a stray byte must be a mismatch, not a 500."""
    assert _token_matches("s3cret", "s3cret")
    assert not _token_matches("s3cr\xe9t", "s3cret")
    assert not _token_matches("s3cret", "s3cr\xe9t")
    assert _token_matches("s3cr\xe9t", "s3cr\xe9t")
    assert not _token_matches(None, "s3cret")


def test_configured_token_accepts_correct_bearer_token(token):
    token("s3cret")
    with TestClient(app) as client:
        resp = client.get("/api/trading/mode",
                          headers={"Authorization": "Bearer s3cret"})
        assert resp.status_code == 200


def test_configured_token_admits_a_remote_caller_who_has_it(token):
    """The token is what makes serving another machine legitimate."""
    token("s3cret")
    with TestClient(app, client=REMOTE, base_url="https://meridian.example") as client:
        assert client.get("/api/trading/mode").status_code == 401
        resp = client.get("/api/trading/mode",
                          headers={"Authorization": "Bearer s3cret"})
        assert resp.status_code == 200


def test_root_and_docs_are_always_exempt(token):
    token("s3cret")
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/docs").status_code == 200


def test_execution_endpoint_is_protected_like_everything_else(token):
    """The endpoint that can place a real order must never be reachable
    without the token once one is configured — no accidental exemption."""
    token("s3cret")
    with TestClient(app) as client:
        resp = client.post("/api/trades/does-not-exist/execute",
                           json={"confirmed": True})
        assert resp.status_code == 401


def test_websocket_takes_the_token_as_a_query_parameter(token):
    token("s3 cret+/=")
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/ws/stream"):
                pass
        assert closed.value.code == 4401
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/stream?token=nope"):
                pass
        # URL-encoded, as a browser's encodeURIComponent would send it.
        with client.websocket_connect("/ws/stream?token=s3%20cret%2B%2F%3D") as socket:
            assert socket.receive_json()["type"] == "hello"


# ── bind address ──────────────────────────────────────────────

@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.53"])
def test_loopback_binds_need_no_token(host):
    assert is_loopback_host(host)
    assert_safe_bind(host, None)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "meridian.example", ""])
def test_network_binds_are_refused_without_a_token(host):
    assert not is_loopback_host(host)
    with pytest.raises(InsecureBindError):
        assert_safe_bind(host, None)
    with pytest.raises(InsecureBindError):
        assert_safe_bind(host, "   ")
    assert_safe_bind(host, "a-real-token")


def test_app_refuses_to_start_on_a_network_host_without_a_token(token, monkeypatch):
    """However it was launched — this is the lifespan check, not the CLI one."""
    token(None)
    monkeypatch.setattr(settings, "api_host", "0.0.0.0")
    with pytest.raises(InsecureBindError):
        with TestClient(app):
            pass


def test_launcher_exits_instead_of_serving_an_open_network_bind(token, monkeypatch, capsys):
    import uvicorn

    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append((a, k)))
    # The launcher writes both of these; register them so they are restored.
    monkeypatch.setenv("API_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "api_host", "127.0.0.1")
    token(None)

    assert serve.main(["--host", "0.0.0.0"]) == 2
    assert started == []
    assert "APP_ACCESS_TOKEN" in capsys.readouterr().err

    assert serve.main([]) == 0
    assert started[0][1]["host"] == "127.0.0.1"

    token("a-real-token")
    assert serve.main(["--host", "0.0.0.0", "--port", "9000"]) == 0
    assert started[1][1] == {"host": "0.0.0.0", "port": 9000, "reload": False}
