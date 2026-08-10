"""Provider selection.

Resolution is per symbol, so one session can stream real crypto from Binance,
real Indian futures from Zerodha, and fall back to the simulator for anything
neither can serve. The trader always sees which is which — a chart backed by
simulated data must never be mistaken for a live one.
"""
from __future__ import annotations

import logging
from typing import Dict, List

from ... import runtime_settings
from .base import MarketDataProvider
from .synthetic import SyntheticProvider

log = logging.getLogger("meridian.providers")


def build_providers() -> Dict[str, MarketDataProvider]:
    cfg = runtime_settings.get()
    providers: Dict[str, MarketDataProvider] = {"synthetic": SyntheticProvider()}
    mode = (cfg.market_provider or "synthetic").lower()

    if mode == "binance" or cfg.hybrid_crypto_live:
        try:
            from .binance import BinanceProvider

            providers["binance"] = BinanceProvider()
        except Exception as exc:  # noqa: BLE001
            log.warning("binance provider unavailable: %s", exc)

    # CoinDCX needs no key and covers the INR books Binance does not, so it is
    # offered whenever live crypto is wanted at all.
    if mode in ("coindcx", "binance") or cfg.hybrid_crypto_live:
        try:
            from .coindcx import CoinDCXProvider

            providers["coindcx"] = CoinDCXProvider()
        except Exception as exc:  # noqa: BLE001
            log.warning("coindcx provider unavailable: %s", exc)

    if mode == "twelvedata" and cfg.twelvedata_api_key:
        try:
            from .twelvedata import TwelveDataProvider

            providers["twelvedata"] = TwelveDataProvider()
        except Exception as exc:  # noqa: BLE001
            log.warning("twelvedata provider unavailable: %s", exc)

    # Zerodha is offered whenever it is authenticated, regardless of the
    # selected mode, because it is the only source for Indian instruments.
    if runtime_settings.store.zerodha_ready:
        try:
            from .zerodha import ZerodhaProvider

            providers["zerodha"] = ZerodhaProvider()
        except Exception as exc:  # noqa: BLE001
            log.warning("zerodha provider unavailable: %s", exc)

    # Angel One, same reasoning as Zerodha: a trader who has it connected
    # (originally for live execution) gets real Indian-market data from it
    # too, with no separate subscription. Checked live (`angel_auth.connected`)
    # rather than "configured", since credentials alone don't mean a session
    # exists yet.
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
) -> MarketDataProvider:
    cfg = runtime_settings.get()
    mode = (cfg.market_provider or "synthetic").lower()

    # A real venue always beats the simulator for a symbol it can serve.
    zerodha = providers.get("zerodha")
    if zerodha is not None and zerodha.supports(symbol):
        return zerodha

    angelone = providers.get("angelone")
    if angelone is not None and angelone.supports(symbol):
        return angelone

    if cfg.hybrid_crypto_live or mode in ("binance", "coindcx"):
        # CoinDCX first: it is the venue the trader actually holds an account
        # on, and it is the only source for the INR-quoted books.
        if mode != "binance":
            coindcx = providers.get("coindcx")
            if coindcx is not None and coindcx.supports(symbol):
                return coindcx
        binance = providers.get("binance")
        if binance is not None and binance.supports(symbol):
            return binance
        coindcx = providers.get("coindcx")
        if coindcx is not None and coindcx.supports(symbol):
            return coindcx

    preferred = providers.get(mode)
    if preferred is not None and preferred.supports(symbol):
        return preferred

    return providers["synthetic"]


def provider_assignments(
    symbols: List[str], providers: Dict[str, MarketDataProvider]
) -> Dict[str, List[str]]:
    """Group symbols by the provider that will serve them."""
    out: Dict[str, List[str]] = {}
    for sym in symbols:
        provider = resolve_provider(sym, providers)
        out.setdefault(provider.name, []).append(sym)
    return out
