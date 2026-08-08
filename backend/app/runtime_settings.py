"""Runtime settings — the values a trader changes from the UI.

`config.py` holds boot-time defaults from the environment. This module holds
the layer on top: the Claude key, the data provider, broker credentials and
risk parameters, all editable at runtime and persisted to
`backend/data/settings.json`.

Secrets are stored in plain JSON on the local disk. That is a deliberate
trade-off for a single-user desktop tool — it is the same trust level as a
browser password store — but it means the file must not be committed or synced.
It is written with 0600 permissions and is in .gitignore.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from .config import DATA_DIR, settings as env_settings

log = logging.getLogger("meridian.settings")

SETTINGS_PATH = DATA_DIR / "settings.json"

#: Fields never returned in full over the API.
SECRET_FIELDS = {
    "anthropic_api_key", "twelvedata_api_key",
    "zerodha_api_secret", "zerodha_access_token",
}


class RuntimeSettings(BaseModel):
    # ── analyst ───────────────────────────────────────────────
    anthropic_api_key: Optional[str] = None
    claude_model: str = "claude-sonnet-5"
    claude_effort: str = "medium"
    ai_auto_analysis_interval: int = 45

    # ── market data ───────────────────────────────────────────
    market_provider: str = "synthetic"   # synthetic | coindcx | binance | zerodha | twelvedata
    hybrid_crypto_live: bool = True
    twelvedata_api_key: Optional[str] = None

    # ── zerodha (kite connect) ────────────────────────────────
    zerodha_api_key: Optional[str] = None
    zerodha_api_secret: Optional[str] = None
    #: Expires daily at ~06:00 IST; re-run the login flow each morning.
    zerodha_access_token: Optional[str] = None
    zerodha_token_issued_at: Optional[float] = None

    # ── risk ──────────────────────────────────────────────────
    account_balance: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    #: Move the tracked stop up the protection ladder automatically as a trade
    #: runs. On by default: the whole point is that protection happens without
    #: waiting for the trader to notice. It only ever moves this tracker —
    #: mirroring it at the broker is confirmed separately and explicitly.
    auto_protect: bool = True
    account_currency: str = "USD"

    def masked(self) -> Dict[str, Any]:
        """Serialisation for the API — secrets become a set/unset hint."""
        data = self.model_dump()
        for field in SECRET_FIELDS:
            value = data.get(field)
            data[field] = ("••••" + value[-4:]) if value else None
            data[field + "_set"] = bool(value)
        return data


class SettingsStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._settings = self._load()
        self._listeners: List[Callable[[RuntimeSettings, set], None]] = []

    # ── persistence ───────────────────────────────────────────

    def _load(self) -> RuntimeSettings:
        # Environment provides the initial values so an existing .env keeps working.
        base = RuntimeSettings(
            anthropic_api_key=env_settings.anthropic_api_key,
            claude_model=env_settings.claude_model,
            claude_effort=env_settings.claude_effort,
            ai_auto_analysis_interval=env_settings.ai_auto_analysis_interval,
            market_provider=env_settings.market_provider,
            hybrid_crypto_live=env_settings.hybrid_crypto_live,
            twelvedata_api_key=env_settings.twelvedata_api_key,
            account_balance=env_settings.account_balance,
            risk_per_trade_pct=env_settings.risk_per_trade_pct,
            auto_protect=env_settings.auto_protect,
            account_currency=env_settings.account_currency,
        )
        if not SETTINGS_PATH.exists():
            return base
        try:
            stored = json.loads(SETTINGS_PATH.read_text())
        except (ValueError, OSError) as exc:
            log.warning("settings.json unreadable (%s); using environment defaults", exc)
            return base

        merged = base.model_dump()
        # A saved value wins over the environment — the UI is the newer intent.
        #
        # An *empty* stored value does not, though. settings.json is written in
        # full on every save, so a key the user has never typed is persisted as
        # "" — and treating that as intent silently masked a perfectly good
        # ANTHROPIC_API_KEY sitting in .env. Blank means "never configured",
        # not "deliberately cleared", so it falls through to the environment.
        for key, value in stored.items():
            if key not in merged or value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            merged[key] = value
        try:
            return RuntimeSettings(**merged)
        except Exception as exc:  # noqa: BLE001
            log.warning("settings.json invalid (%s); using environment defaults", exc)
            return base

    def _persist(self) -> None:
        try:
            SETTINGS_PATH.write_text(json.dumps(self._settings.model_dump(), indent=2))
            os.chmod(SETTINGS_PATH, 0o600)
        except OSError as exc:
            log.warning("could not persist settings: %s", exc)

    # ── access ────────────────────────────────────────────────

    @property
    def current(self) -> RuntimeSettings:
        with self._lock:
            return self._settings

    def update(self, patch: Dict[str, Any]) -> RuntimeSettings:
        """Apply a partial update. Empty strings clear a value; absent keys keep it."""
        with self._lock:
            data = self._settings.model_dump()
            changed = set()
            for key, value in patch.items():
                if key not in data:
                    continue
                # The UI sends back the masked placeholder for untouched secrets.
                if key in SECRET_FIELDS and isinstance(value, str) and value.startswith("••••"):
                    continue
                if isinstance(value, str) and value.strip() == "" and key in SECRET_FIELDS:
                    value = None
                if data[key] != value:
                    data[key] = value
                    changed.add(key)

            if not changed:
                return self._settings

            self._settings = RuntimeSettings(**data)
            self._persist()

        log.info("settings updated: %s", ", ".join(sorted(changed)))
        for listener in list(self._listeners):
            try:
                listener(self._settings, changed)
            except Exception as exc:  # noqa: BLE001 - a bad listener must not block a save
                log.warning("settings listener failed: %s", exc)
        return self._settings

    def on_change(self, listener: Callable[[RuntimeSettings, set], None]) -> None:
        self._listeners.append(listener)

    # ── convenience ───────────────────────────────────────────

    @property
    def claude_enabled(self) -> bool:
        key = self.current.anthropic_api_key
        return bool(key and key.strip())

    @property
    def zerodha_ready(self) -> bool:
        s = self.current
        return bool(s.zerodha_api_key and s.zerodha_access_token)


store = SettingsStore()


def get() -> RuntimeSettings:
    return store.current
