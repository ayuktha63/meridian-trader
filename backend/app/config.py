"""Environment-driven settings.

Every setting has a working default: the platform must boot with no .env,
no database, no Redis and no API keys.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from typing_extensions import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── claude ────────────────────────────────────────────────
    anthropic_api_key: Optional[str] = None
    claude_model: str = "claude-sonnet-5"
    # Opus 5 thinks by default and max_tokens caps thinking + response text
    # together, so this needs real headroom even for a small JSON verdict.
    claude_max_tokens: int = 16_000
    # low | medium | high | xhigh | max. Opus 5 is unusually strong at medium,
    # which is the right trade for an analyst re-running every ~45 seconds.
    claude_effort: str = "medium"
    ai_auto_analysis_interval: int = 45

    # ── market data ───────────────────────────────────────────
    market_provider: str = "synthetic"
    twelvedata_api_key: Optional[str] = None
    hybrid_crypto_live: bool = True
    feed_tick_hz: float = 2.0
    history_bars: int = 1500

    # ── storage ───────────────────────────────────────────────
    database_url: Optional[str] = None
    redis_url: Optional[str] = None

    # ── server ────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # `NoDecode` is load-bearing. Without it pydantic-settings treats any
    # complex-typed field as JSON and tries to decode the env value *before*
    # validators run — so a comma-separated CORS_ORIGINS raises SettingsError
    # at import time and the app never boots. That made `cp .env.example .env`,
    # the first step in the README, fatal.
    cors_origins: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://localhost:4173",
            "http://127.0.0.1:5173",
            "app://.",
        ]
    )
    log_level: str = "INFO"

    # ── risk ──────────────────────────────────────────────────
    account_balance: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    auto_protect: bool = True
    account_currency: str = "USD"

    # ── angel one (live execution) ───────────────────────────
    # Boot-time only, deliberately not in runtime_settings.py: that store
    # persists to a plain-JSON file readable over the settings API (masked,
    # but still round-trippable in-process). Credentials that can submit a
    # real order don't belong anywhere the frontend can reach, even masked.
    trading_mode: str = "PAPER"          # PAPER | LIVE — never auto-switches
    angel_api_key: Optional[str] = None
    angel_client_code: Optional[str] = None
    angel_password: Optional[str] = None
    angel_totp_secret: Optional[str] = None

    @field_validator("trading_mode", mode="before")
    @classmethod
    def _upper_trading_mode(cls, v):
        return v.upper() if isinstance(v, str) else v

    @property
    def angel_configured(self) -> bool:
        return bool(self.angel_api_key and self.angel_client_code
                    and self.angel_password and self.angel_totp_secret)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        """Accept either a comma-separated string or a real list.

        Vite falls forward to 5174, 5175… when 5173 is taken, so any localhost
        port is allowed in addition to whatever is configured — a CORS failure
        on a port the dev server chose by itself is pure friction with no
        security value on a loopback interface.
        """
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return "sqlite+aiosqlite:///" + str(DATA_DIR / "meridian.db")

    @property
    def claude_enabled(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Manual .env load keeps behaviour identical when the app is imported
    # from a different working directory (tests, Electron, workers).
    for candidate in (REPO_DIR / ".env", BACKEND_DIR / ".env"):
        if candidate.exists():
            try:
                from dotenv import load_dotenv

                load_dotenv(candidate, override=False)
            except ImportError:  # pragma: no cover - dotenv is a hard dep
                pass
            break
    return Settings()


settings = get_settings()

# Silence the noisiest third-party loggers before anything imports them.
os.environ.setdefault("PYTHONWARNINGS", "ignore::DeprecationWarning")
