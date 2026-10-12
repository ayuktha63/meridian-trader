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
#: Everything the app writes — the SQLite file, settings.json, the persisted
#: kill switch. Overridable so a test run (or a second instance) can never
#: read or write the state of the one that is actually trading.
DATA_DIR = Path(os.environ.get("MERIDIAN_DATA_DIR") or (BACKEND_DIR / "data"))
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
    # Loopback by default. This API can place real orders, so being reachable
    # from the network is something you opt into — and `security.py` refuses
    # to serve a non-loopback host unless APP_ACCESS_TOKEN is set.
    api_host: str = "127.0.0.1"
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
    #: Reward-to-risk floor for the auto-trader. 2.0 is the value the
    #: parameter sweep supported; see `analysis/autotrader.py`.
    min_risk_reward: float = 2.0

    # ── live order supervision ───────────────────────────────
    #: The loop that watches every live order and position at the broker and
    #: keeps a stop resting behind each fill. Only ever turned off by tests,
    #: which drive single passes by hand.
    live_reconciler_enabled: bool = True
    live_reconcile_interval_seconds: float = 3.0
    #: An entry is a LIMIT at the price the analysis saw. If the market has
    #: not come back to it in this long, the setup it was sized for is gone
    #: and the order is cancelled rather than left to fill hours later.
    #: 0 disables the timeout.
    live_entry_timeout_seconds: float = 300.0

    # ── live risk limits ─────────────────────────────────────
    # Hard limits on real orders, in the account's currency. Read once at
    # boot from the environment and nowhere editable from the UI: a limit
    # that can be raised with a click while LIVE is not a limit. To change
    # one, edit .env and restart — which also drops you back to PAPER.
    #: A live order never risks more than this share of the cash Angel One
    #: reports as available, whatever the recommendation was sized for. The
    #: recommendation's own budget comes from the balance typed into
    #: Settings, which is not the account.
    live_max_risk_pct_of_funds: float = 1.0
    live_max_order_value: float = 50_000.0
    live_max_quantity: int = 5_000
    live_max_open_positions: int = 2
    live_max_trades_per_day: int = 5
    #: Realised loss for the IST trading day at which the kill switch trips.
    live_daily_loss_limit: float = 2_000.0
    live_max_symbol_exposure: float = 50_000.0
    live_max_sector_exposure: float = 100_000.0
    #: Furthest the entry may sit from the broker's last traded price, in
    #: percent. Also the stale-recommendation check. 0 disables.
    live_price_band_pct: float = 1.0
    live_enforce_market_hours: bool = True
    #: Orders are intraday. Close what is still open this long before the
    #: session ends, ahead of the broker's own square-off, and take no new
    #: entry inside the longer window — it would be closed almost at once.
    live_squareoff_minutes_before_close: float = 20.0
    live_no_entry_minutes_before_close: float = 45.0
    #: Refuse a live order unless a recent walk-forward test of this symbol
    #: and timeframe showed positive expectancy after costs.
    live_require_measured_edge: bool = True
    #: Set to true only after one minimum-size order has been placed and
    #: watched end to end in Angel One's own app (TRADING.md §3a). Until
    #: then no strategy is eligible for live review, whatever its results:
    #: the order path itself is unverified.
    live_broker_validated: bool = False

    # ── alert channel (optional) ──────────────────────────────
    #: Critical and warning alerts are also sent here when both are set.
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

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

    # ── app access (only matters once this is reachable off localhost) ──
    # Unset by default — a bare `make dev` on your own machine needs no
    # login, because without a token the API only answers loopback callers.
    # Set it to serve anyone else: a non-loopback API_HOST will not start
    # without it, and a request arriving from another machine or through a
    # proxy is refused until it carries it.
    app_access_token: Optional[str] = None

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
