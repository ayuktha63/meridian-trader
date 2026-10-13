"""Settings API — everything the trader configures from the UI.

Changes apply immediately: saving an Anthropic key swaps the analyst on the
next analysis. Nothing here needs a restart.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from .. import runtime_settings
from ..ai import claude_client
from ..broker.angelone import angel_auth
from ..data.feed import feed
from ..instruments import INSTRUMENTS

log = logging.getLogger("meridian.api.settings")
router = APIRouter(prefix="/api/settings", tags=["settings"])

#: Changing any of these invalidates the cached Anthropic client.
CLAUDE_FIELDS = {"anthropic_api_key", "claude_model", "claude_effort"}


class SettingsPatch(BaseModel):
    anthropic_api_key: Optional[str] = None
    claude_model: Optional[str] = None
    claude_effort: Optional[str] = None
    ai_auto_analysis_interval: Optional[int] = None
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


# ── status helpers ────────────────────────────────────────────

def _provider_status() -> List[Dict[str, Any]]:
    active = set(feed.providers)
    return [
        {
            "id": "angelone",
            "label": "Angel One SmartAPI",
            "live": True,
            "ready": angel_auth.connected,
            "active": "angelone" in active,
            "needs": ["Connect Angel One — the DEMO / LIVE control in the top bar"],
            "covers": "NSE F&O stocks, NIFTY and BANK NIFTY, the INR currency futures "
                      "and MCX gold, silver and crude — quotes, history and the list "
                      "of stocks itself, from the broker connection that places the "
                      "orders. There is no other source: until it is connected, "
                      "nothing has data.",
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
            "live": feed.is_live(inst.symbol),
        }
        for inst in INSTRUMENTS.values()
    ]
