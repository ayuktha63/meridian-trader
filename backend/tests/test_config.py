from __future__ import annotations

import importlib
import os

import pytest


def _fresh_settings(monkeypatch, **env):
    """Build a Settings instance from a clean environment."""
    from app import config

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    importlib.reload(config)
    return config.Settings()


def test_comma_separated_cors_origins_parse(monkeypatch):
    """The bug this guards: pydantic-settings JSON-decodes any complex-typed
    field straight from the environment, before validators run. A plain
    comma-separated CORS_ORIGINS therefore raised SettingsError at import time
    and the app never started — which made `cp .env.example .env`, the first
    step in the README, fatal."""
    settings = _fresh_settings(
        monkeypatch,
        CORS_ORIGINS="http://localhost:5173,http://localhost:4173,app://.")
    assert settings.cors_origins == [
        "http://localhost:5173", "http://localhost:4173", "app://."]


def test_cors_origins_tolerates_spacing(monkeypatch):
    settings = _fresh_settings(
        monkeypatch, CORS_ORIGINS=" http://a.test , http://b.test ,, ")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_falls_back_to_defaults(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    from app import config

    importlib.reload(config)
    settings = config.Settings()
    assert "http://localhost:5173" in settings.cors_origins


def test_the_shipped_env_example_actually_boots(monkeypatch):
    """`cp .env.example .env` is the documented first step, so every key in
    that file has to survive being loaded. Parse it and feed each value in."""
    from pathlib import Path

    from app import config

    example = Path(config.REPO_DIR) / ".env.example"
    assert example.exists(), "the documented template is missing"

    for line in example.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if value.strip():
            monkeypatch.setenv(key.strip(), value.strip())

    importlib.reload(config)
    settings = config.Settings()          # must not raise
    assert settings.api_port == 8000
    assert settings.cors_origins
