"""AccessTokenMiddleware — the gate that has to exist before this app is
ever reachable off localhost."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture(autouse=True)
def _reset_token():
    original = settings.app_access_token
    yield
    settings.app_access_token = original


def test_no_token_configured_means_no_auth_required():
    settings.app_access_token = None
    with TestClient(app) as client:
        resp = client.get("/api/trading/mode")
        assert resp.status_code == 200


def test_configured_token_rejects_unauthenticated_requests():
    settings.app_access_token = "s3cret"
    with TestClient(app) as client:
        resp = client.get("/api/trading/mode")
        assert resp.status_code == 401


def test_configured_token_rejects_wrong_token():
    settings.app_access_token = "s3cret"
    with TestClient(app) as client:
        resp = client.get("/api/trading/mode",
                          headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401


def test_configured_token_accepts_correct_bearer_token():
    settings.app_access_token = "s3cret"
    with TestClient(app) as client:
        resp = client.get("/api/trading/mode",
                          headers={"Authorization": "Bearer s3cret"})
        assert resp.status_code == 200


def test_root_and_docs_are_always_exempt():
    settings.app_access_token = "s3cret"
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/docs").status_code == 200


def test_execution_endpoint_is_protected_like_everything_else():
    """The endpoint that can place a real order must never be reachable
    without the token once one is configured — no accidental exemption."""
    settings.app_access_token = "s3cret"
    with TestClient(app) as client:
        resp = client.post("/api/trades/does-not-exist/execute",
                           json={"confirmed": True})
        assert resp.status_code == 401
