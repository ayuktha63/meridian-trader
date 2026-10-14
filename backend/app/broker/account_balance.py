"""Which account balance sizing and account-risk limits should use.

DEMO always uses the Settings value — there is no wallet to size against.
LIVE should use what Angel One actually reports as available, so a trader who
switches modes sees their own money, not the number they last typed into
Settings. If Angel One can't be asked right now (no session, a stale one, a
flaky response) sizing falls back to the Settings value exactly as it did
before this existed — a quiet degrade, never a blocked analysis.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from .. import runtime_settings
from ..cache import cache
from .angelone.auth import angel_auth
from .angelone.client import angel_broker
from .trading_mode import trading_mode

log = logging.getLogger("meridian.broker.account_balance")

CACHE_KEY = "angel_one:available_funds"
CACHE_TTL_SECONDS = 15


def extract_available_funds(funds: Optional[dict]) -> Optional[float]:
    """Angel One's rmsLimit response. `availablecash` is the conservative,
    non-leveraged figure — deliberately not `net` or a margin-inclusive one."""
    if not funds:
        return None
    for key in ("availablecash", "net"):
        value = funds.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


async def effective_balance() -> float:
    """The balance to size and gate against right now."""
    value, _source = await effective_balance_with_source()
    return value


async def effective_balance_with_source() -> Tuple[float, str]:
    """Same as `effective_balance`, plus where the number came from — so the
    UI can say "this is your wallet" instead of implying it always is."""
    cfg = runtime_settings.get()
    if not trading_mode.is_live or not angel_auth.connected:
        return cfg.account_balance, "settings"

    cached = await cache.get(CACHE_KEY)
    if cached is not None:
        try:
            return float(cached), "angel_one"
        except (TypeError, ValueError):
            pass

    try:
        funds = await angel_broker.get_funds()
        value = extract_available_funds(funds)
    except Exception as exc:  # noqa: BLE001 - a flaky funds call must never break sizing
        log.warning("could not fetch Angel One funds for sizing: %s", exc)
        value = None

    if value is None:
        return cfg.account_balance, "settings"

    await cache.set(CACHE_KEY, str(value), ttl=CACHE_TTL_SECONDS)
    return value, "angel_one"
