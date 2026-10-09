"""Outbound alert channel: Telegram, when configured.

A stop that could not be placed is not something to find out about by
looking at the screen. Sending is fire-and-forget on a worker thread so a
slow or dead Telegram can never delay the action that raised the alert.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict

import httpx

from . import alerts
from .config import settings

log = logging.getLogger("meridian.notify")
_ICON = {"critical": "\U0001F6A8", "warning": "\u26A0\uFE0F"}


def _send(alert: Dict[str, Any]) -> None:
    text = "{} Meridian {}\n{}".format(
        _ICON.get(alert["level"], ""), alert["code"], alert["message"])
    if alert.get("context"):
        text += "\n" + ", ".join("{}={}".format(k, v) for k, v in alert["context"].items())
    try:
        httpx.post(
            "https://api.telegram.org/bot{}/sendMessage".format(settings.telegram_bot_token),
            json={"chat_id": settings.telegram_chat_id, "text": text}, timeout=10.0)
    except Exception as exc:  # noqa: BLE001 - never include the URL: it holds the token
        log.warning("telegram alert failed: %s", type(exc).__name__)


def telegram_sink(alert: Dict[str, Any]) -> None:
    if alert["level"] == "info":
        return
    threading.Thread(target=_send, args=(alert,), daemon=True).start()


def install() -> bool:
    if settings.telegram_bot_token and settings.telegram_chat_id:
        alerts.add_sink(telegram_sink)
        return True
    return False
