"""Provider selection.

Angel One is the only market-data source: quotes, history and the list of
what can be traded all come from the broker connection that places the
orders. There is nothing behind it. A symbol it cannot serve — no session,
or not something it lists — has no data, and the chart says so rather than
drawing bars from somewhere else.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .base import MarketDataProvider

log = logging.getLogger("meridian.providers")


def build_providers() -> Dict[str, MarketDataProvider]:
    providers: Dict[str, MarketDataProvider] = {}

    # Asked of the session itself rather than of the configuration:
    # credentials on file are not a connection.
    from ...broker.angelone.auth import angel_auth
    if angel_auth.connected:
        try:
            from .angelone import AngelOneMarketDataProvider

            providers["angelone"] = AngelOneMarketDataProvider()
        except Exception as exc:  # noqa: BLE001
            log.warning("angelone provider unavailable: %s", exc)

    return providers


def resolve_provider(
    symbol: str, providers: Dict[str, MarketDataProvider]
) -> Optional[MarketDataProvider]:
    """The source that serves this symbol, or None when nothing does."""
    for provider in providers.values():
        if provider.supports(symbol):
            return provider
    return None


def provider_assignments(
    symbols: List[str], providers: Dict[str, MarketDataProvider]
) -> Dict[str, List[str]]:
    """Group symbols by the provider that will serve them. A symbol nothing
    serves is in no group."""
    out: Dict[str, List[str]] = {}
    for sym in symbols:
        provider = resolve_provider(sym, providers)
        if provider is not None:
            out.setdefault(provider.name, []).append(sym)
    return out
