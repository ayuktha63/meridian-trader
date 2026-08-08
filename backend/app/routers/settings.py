"""Settings API — everything the trader configures from the UI.

Changes apply immediately: saving an Anthropic key swaps the analyst on the
next analysis, and saving broker credentials rebuilds the market feed. Nothing
here needs a restart.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from .. import runtime_settings
from ..ai import claude_client
from ..data.feed import feed
from ..instruments import INSTRUMENTS, SYMBOLS

log = logging.getLogger("meridian.api.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])

#: Changing any of these means the market feed has to be rebuilt.
FEED_FIELDS = {
    "market_provider", "hybrid_crypto_live", "twelvedata_api_key",
    "zerodha_api_key", "zerodha_api_secret", "zerodha_access_token",
}
#: Changing any of these invalidates the cached Anthropic client.
CLAUDE_FIELDS = {"anthropic_api_key", "claude_model", "claude_effort"}


class SettingsPatch(BaseModel):
    anthropic_api_key: Optional[str] = None
    claude_model: Optional[str] = None
    claude_effort: Optional[str] = None
    ai_auto_analysis_interval: Optional[int] = None
    market_provider: Optional[str] = None
    hybrid_crypto_live: Optional[bool] = None
    twelvedata_api_key: Optional[str] = None
    zerodha_api_key: Optional[str] = None
    zerodha_api_secret: Optional[str] = None
    account_balance: Optional[float] = None
    risk_per_trade_pct: Optional[float] = None
    account_currency: Optional[str] = None


@router.get("")
async def read_settings() -> Dict[str, Any]:
    cfg = runtime_settings.get()
    return {
        "settings": cfg.masked(),
        "analyst": claude_client.availability(),
        "providers": _provider_status(),
        "instruments": _instrument_status(),
    }


@router.put("")
async def update_settings(patch: SettingsPatch = Body(...)) -> Dict[str, Any]:
    payload = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not payload:
        return await read_settings()

    before = runtime_settings.get().model_dump()
    runtime_settings.store.update(payload)
    after = runtime_settings.get().model_dump()
    changed = {k for k in after if before.get(k) != after.get(k)}

    if changed & CLAUDE_FIELDS:
        claude_client.reset()
    if changed & FEED_FIELDS:
        # Rebuilt in the background so the request returns immediately; the UI
        # polls provider status to see the result.
        asyncio.create_task(feed.reload("provider settings changed"))

    return await read_settings()


@router.post("/test-claude")
async def test_claude() -> Dict[str, Any]:
    """Round-trip a tiny request so a bad key fails here, not mid-analysis."""
    if not runtime_settings.store.claude_enabled:
        raise HTTPException(status_code=400, detail="No Anthropic API key saved")

    claude_client.reset()
    cfg = runtime_settings.get()
    try:
        import anthropic
    except ImportError:
        raise HTTPException(status_code=500, detail="anthropic package is not installed")

    client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
    try:
        response = await client.messages.create(
            model=cfg.claude_model,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=401, detail="That API key was rejected by Anthropic")
    except anthropic.NotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Model '{}' is not available to this key".format(cfg.claude_model))
    except anthropic.APIStatusError as exc:
        raise HTTPException(status_code=502, detail=exc.message)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc))
    finally:
        await client.close()

    text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
    return {
        "ok": True,
        "model": response.model,
        "reply": text.strip()[:40],
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }


# ── Zerodha login flow ────────────────────────────────────────

@router.get("/zerodha/login-url")
async def zerodha_login_url() -> Dict[str, str]:
    cfg = runtime_settings.get()
    if not cfg.zerodha_api_key:
        raise HTTPException(status_code=400, detail="Save your Kite API key first")
    from ..data.providers.zerodha import login_url

    return {
        "url": login_url(cfg.zerodha_api_key),
        "instructions": (
            "Open this URL, log in to Kite, and approve. You will be redirected to "
            "your app's redirect URL with ?request_token=... in the address bar. "
            "Copy that token and paste it below."
        ),
    }


@router.post("/zerodha/exchange")
async def zerodha_exchange(request_token: str = Body(..., embed=True)) -> Dict[str, Any]:
    """Swap a one-time request_token for the day's access_token."""
    cfg = runtime_settings.get()
    if not cfg.zerodha_api_key or not cfg.zerodha_api_secret:
        raise HTTPException(status_code=400, detail="Save your Kite API key and secret first")

    from ..data.providers.zerodha import ZerodhaError, exchange_request_token

    loop = asyncio.get_running_loop()
    try:
        session = await loop.run_in_executor(
            None, exchange_request_token,
            cfg.zerodha_api_key, cfg.zerodha_api_secret, request_token.strip())
    except ZerodhaError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400,
            detail="Kite rejected that request token ({}). They are single-use and "
                   "expire within minutes — generate a fresh one.".format(exc))

    import time

    runtime_settings.store.update({
        "zerodha_access_token": session["access_token"],
        "zerodha_token_issued_at": time.time(),
    })
    asyncio.create_task(feed.reload("zerodha session established"))

    return {
        "ok": True,
        "user_id": session.get("user_id"),
        "user_name": session.get("user_name"),
        "note": "Access tokens expire daily around 06:00 IST — repeat this each morning.",
    }


@router.post("/zerodha/disconnect")
async def zerodha_disconnect() -> Dict[str, Any]:
    runtime_settings.store.update({
        "zerodha_access_token": "", "zerodha_token_issued_at": None,
    })
    asyncio.create_task(feed.reload("zerodha disconnected"))
    return {"ok": True}


@router.get("/zerodha/contracts")
async def zerodha_contracts() -> List[Dict[str, Any]]:
    """Which Kite contract each Indian symbol currently resolves to."""
    provider = feed.providers.get("zerodha")
    if provider is None:
        raise HTTPException(status_code=400, detail="Zerodha is not connected")

    out: List[Dict[str, Any]] = []
    for symbol in SYMBOLS:
        instrument = INSTRUMENTS[symbol]
        if not instrument.exchange:
            continue
        contract = await provider.contract_for(symbol)   # type: ignore[attr-defined]
        out.append({
            "symbol": symbol,
            "display": instrument.display,
            "resolved": contract["tradingsymbol"] if contract else None,
            "exchange": contract["exchange"] if contract else instrument.exchange,
            "expiry": contract.get("expiry") if contract else None,
            "lot_size": contract.get("lot_size") if contract else instrument.contract_size,
            "error": None if contract else provider.last_error,  # type: ignore[attr-defined]
        })
    return out


# ── status helpers ────────────────────────────────────────────

def _provider_status() -> List[Dict[str, Any]]:
    cfg = runtime_settings.get()
    active = set(feed.providers)
    return [
        {
            "id": "synthetic",
            "label": "Simulated",
            "live": False,
            "ready": True,
            "active": "synthetic" in active,
            "needs": [],
            "covers": "Every symbol — realistic but not real. For learning the tool only.",
        },
        {
            "id": "coindcx",
            "label": "CoinDCX (crypto)",
            "live": True,
            "ready": True,
            "active": "coindcx" in active,
            "needs": [],
            "covers": "12 USDT pairs plus the INR books (BTC/ETH/SOL/XRP/DOGE) — "
                      "the exchange you already hold. Public data, no keys needed; "
                      "Meridian never places an order.",
        },
        {
            "id": "binance",
            "label": "Binance (crypto)",
            "live": True,
            "ready": True,
            "active": "binance" in active,
            "needs": [],
            "covers": "BTCUSD, ETHUSD — genuinely live, no credentials needed. "
                      "Deeper history than CoinDCX for backtesting.",
        },
        {
            "id": "zerodha",
            "label": "Zerodha Kite Connect",
            "live": True,
            "ready": runtime_settings.store.zerodha_ready,
            "active": "zerodha" in active,
            "needs": ["zerodha_api_key", "zerodha_api_secret", "daily login"],
            "covers": "USDINR/EURINR/GBPINR/JPYINR futures, NIFTY, BANKNIFTY, "
                      "MCX GOLD/SILVER/CRUDEOIL — the instruments you can actually "
                      "trade from India.",
        },
        {
            "id": "twelvedata",
            "label": "Twelve Data (spot FX)",
            "live": True,
            "ready": bool(cfg.twelvedata_api_key),
            "active": "twelvedata" in active,
            "needs": ["twelvedata_api_key"],
            "covers": "EURUSD, GBPUSD, XAUUSD and friends. Analysis only for Indian "
                      "residents — spot FX is not permitted under FEMA.",
        },
    ]


def _instrument_status() -> List[Dict[str, Any]]:
    return [
        {
            "symbol": inst.symbol,
            "display": inst.display,
            "asset_class": inst.asset_class,
            "venue": inst.venue,
            "exchange": inst.exchange,
            "broker_symbol": inst.broker_symbol,
            "lot_size": inst.contract_size,
            "tick_size": inst.pip_size,
            "tick_value": round(inst.pip_value_per_lot, 4),
            "tradable_in_india": inst.tradable_in_india,
            "note": inst.note,
            "provider": feed.provider_for(inst.symbol),
            "live": feed.provider_for(inst.symbol) != "synthetic",
        }
        for inst in INSTRUMENTS.values()
    ]
