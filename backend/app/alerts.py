"""Operator alerts — things a human has to know about now.

One place to raise them so every channel hears the same thing. Today that is
the log and a short in-memory history the UI polls; outbound channels
(Telegram, email) register as sinks without the callers changing.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from typing import Any, Callable, Deque, Dict, List

log = logging.getLogger("meridian.alerts")

_LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "critical": logging.CRITICAL}
_history: Deque[Dict[str, Any]] = deque(maxlen=200)
_sinks: List[Callable[[Dict[str, Any]], None]] = []


def add_sink(sink: Callable[[Dict[str, Any]], None]) -> None:
    _sinks.append(sink)


def emit(level: str, code: str, message: str, **context: Any) -> Dict[str, Any]:
    alert = {"id": uuid.uuid4().hex, "level": level, "code": code,
             "message": message, "context": context, "created_at": time.time()}
    _history.append(alert)
    log.log(_LEVELS.get(level, logging.WARNING), "[%s] %s %s", code, message, context or "")
    for sink in list(_sinks):
        try:
            sink(alert)
        except Exception:  # noqa: BLE001 - one dead channel must not mute the rest
            log.exception("alert sink failed")
    return alert


def recent(since: float = 0.0) -> List[Dict[str, Any]]:
    return [a for a in _history if a["created_at"] > since]


def clear() -> None:
    _history.clear()
