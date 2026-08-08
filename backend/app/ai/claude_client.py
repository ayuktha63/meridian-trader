"""Claude analyst client.

Notes on the request shape, because several of these are easy to get wrong on
Claude Opus 5 and fail loudly (or worse, silently):

* **No sampling parameters.** `temperature` / `top_p` / `top_k` are rejected
  with a 400. Behaviour is steered by the prompt instead.
* **Thinking is on by default**, and `max_tokens` caps thinking *plus*
  response text together — hence the generous default in settings. A tight
  cap truncates the JSON mid-object.
* **Structured output** goes through `output_config.format` with a strict
  JSON schema, so the verdict is guaranteed parseable rather than coaxed.
* **The system prompt is cached.** It is frozen and carries a `cache_control`
  breakpoint; every volatile byte lives in the user turn, so repeat requests
  read the prefix at ~0.1x instead of re-paying for it.
* **Refusals arrive as HTTP 200** with `stop_reason == "refusal"` and empty
  content, so `stop_reason` is checked before content is read.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from .. import runtime_settings
from ..config import settings
from ..instruments import Instrument
from ..schemas.analysis import AIVerdict, Decision
from .prompts import SYSTEM_PROMPT, VERDICT_SCHEMA, build_user_message

log = logging.getLogger("meridian.claude")

_client = None
_unavailable_reason: Optional[str] = None


def _get_client():
    """Lazily construct the async client so import never requires a key."""
    global _client, _unavailable_reason
    if _client is not None or _unavailable_reason is not None:
        return _client
    if not runtime_settings.store.claude_enabled:
        _unavailable_reason = "no Anthropic API key configured"
        return None
    try:
        import anthropic

        _client = anthropic.AsyncAnthropic(
            api_key=runtime_settings.get().anthropic_api_key)
    except ImportError:
        _unavailable_reason = "anthropic package not installed"
    except Exception as exc:  # noqa: BLE001
        _unavailable_reason = str(exc)
    return _client


def reset() -> None:
    """Drop the cached client so a newly-saved key takes effect immediately."""
    global _client, _unavailable_reason
    _client = None
    _unavailable_reason = None


def availability() -> Dict[str, Any]:
    cfg = runtime_settings.get()
    return {
        "enabled": runtime_settings.store.claude_enabled,
        "model": cfg.claude_model,
        "effort": cfg.claude_effort,
        "max_tokens": settings.claude_max_tokens,
        "reason": _unavailable_reason,
    }


def _extract_json(content: List[Any]) -> Optional[Dict[str, Any]]:
    for block in content:
        if getattr(block, "type", None) != "text":
            continue
        text = (block.text or "").strip()
        if not text:
            continue
        try:
            return json.loads(text)
        except ValueError:
            # Schema-constrained output should never need this, but a
            # truncated or fenced response is recoverable often enough.
            start, end = text.find("{"), text.rfind("}")
            if 0 <= start < end:
                try:
                    return json.loads(text[start:end + 1])
                except ValueError:
                    continue
    return None


def _coerce(payload: Dict[str, Any], instrument: Instrument,
            latency_ms: int, raw: str) -> AIVerdict:
    def price(key: str) -> Optional[float]:
        value = payload.get(key)
        if value is None:
            return None
        try:
            return round(float(value), instrument.digits)
        except (TypeError, ValueError):
            return None

    def number(key: str, lo: float, hi: float, default: float = 0.0) -> float:
        try:
            return max(lo, min(hi, float(payload.get(key, default))))
        except (TypeError, ValueError):
            return default

    def strings(key: str) -> List[str]:
        value = payload.get(key) or []
        if isinstance(value, str):
            return [value]
        return [str(v) for v in value if v is not None][:8]

    try:
        decision = Decision(str(payload.get("decision", "WAIT")).upper())
    except ValueError:
        decision = Decision.WAIT

    verdict = AIVerdict(
        decision=decision,
        confidence=round(number("confidence", 0.0, 100.0), 1),
        probability=round(number("probability", 0.0, 100.0), 1),
        entry=price("entry"),
        stop_loss=price("stop_loss"),
        take_profit=price("take_profit"),
        take_profit_2=price("take_profit_2"),
        risk_reward=payload.get("risk_reward"),
        trend=str(payload.get("trend", "neutral"))[:120],
        market_structure=str(payload.get("market_structure", "ranging"))[:200],
        bullish_points=strings("bullish_points"),
        bearish_points=strings("bearish_points"),
        key_levels=[round(float(v), instrument.digits)
                    for v in (payload.get("key_levels") or [])
                    if isinstance(v, (int, float))][:8],
        invalidation=(str(payload["invalidation"])[:400]
                      if payload.get("invalidation") else None),
        timeframe_alignment=(str(payload["timeframe_alignment"])[:400]
                             if payload.get("timeframe_alignment") else None),
        summary=str(payload.get("summary", ""))[:2000],
        uncertainty=str(payload.get("uncertainty", ""))[:1000],
        source="claude",
        model=runtime_settings.get().claude_model,
        latency_ms=latency_ms,
        raw=raw[:8000] if raw else None,
    )

    # A directional call with no invalidation level is not actionable.
    if verdict.decision != Decision.WAIT and verdict.stop_loss is None:
        verdict.decision = Decision.WAIT
        verdict.uncertainty = (
            "Downgraded to WAIT: the analyst did not supply a stop level, so the "
            "trade has no defined invalidation. " + (verdict.uncertainty or "")
        ).strip()

    if verdict.risk_reward is None and all(
        v is not None for v in (verdict.entry, verdict.stop_loss, verdict.take_profit)
    ):
        risk = abs(verdict.entry - verdict.stop_loss)
        if risk > 0:
            verdict.risk_reward = round(abs(verdict.take_profit - verdict.entry) / risk, 2)

    return verdict


async def analyse(narrative: str, evidence: Dict[str, Any], instrument: Instrument,
                  question: Optional[str] = None) -> Optional[AIVerdict]:
    """Ask Claude for a verdict. Returns None when the caller should fall back."""
    client = _get_client()
    if client is None:
        return None

    import anthropic

    started = time.perf_counter()
    try:
        response = await client.messages.create(
            model=runtime_settings.get().claude_model,
            max_tokens=settings.claude_max_tokens,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                # Frozen prefix — cached across every analysis request.
                "cache_control": {"type": "ephemeral"},
            }],
            output_config={
                "effort": runtime_settings.get().claude_effort,
                "format": {"type": "json_schema", "schema": VERDICT_SCHEMA},
            },
            messages=[{
                "role": "user",
                "content": build_user_message(narrative, evidence, question),
            }],
        )
    except anthropic.RateLimitError as exc:
        log.warning("claude rate limited: %s", exc)
        return None
    except anthropic.APIStatusError as exc:
        log.warning("claude API error %s: %s", exc.status_code, exc.message)
        return None
    except anthropic.APIConnectionError as exc:
        log.warning("claude connection error: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - never take the platform down
        log.exception("unexpected claude failure: %s", exc)
        return None

    latency_ms = int((time.perf_counter() - started) * 1000)

    # Check the stop reason before touching content — a refusal returns 200
    # with an empty content list.
    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        log.warning("claude declined the request (%s)",
                    getattr(detail, "category", None) or "unspecified")
        return None
    if response.stop_reason == "max_tokens":
        log.warning("claude response hit max_tokens (%s) — verdict may be truncated",
                    settings.claude_max_tokens)

    payload = _extract_json(response.content)
    if payload is None:
        log.warning("claude returned no parseable JSON verdict")
        return None

    usage = getattr(response, "usage", None)
    if usage is not None:
        log.debug(
            "claude verdict in %dms (in=%s cached=%s out=%s)",
            latency_ms, getattr(usage, "input_tokens", "?"),
            getattr(usage, "cache_read_input_tokens", 0),
            getattr(usage, "output_tokens", "?"),
        )

    return _coerce(payload, instrument, latency_ms, json.dumps(payload)[:8000])
