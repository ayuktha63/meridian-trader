"""Cache layer: Redis when configured, in-process TTL map otherwise.

Used for candle snapshots, analysis results and rate limiting. Nothing in
the platform requires Redis to be present.
"""
from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from typing import Any, Optional

from .config import settings

log = logging.getLogger("meridian.cache")


class _MemoryCache:
    """Bounded LRU with per-key TTL."""

    def __init__(self, max_items: int = 2048) -> None:
        self._data: "OrderedDict[str, tuple]" = OrderedDict()
        self._max = max_items

    def get(self, key: str) -> Optional[str]:
        item = self._data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires and expires < time.time():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        self._data[key] = (value, time.time() + ttl if ttl else None)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear_prefix(self, prefix: str) -> None:
        for k in [k for k in self._data if k.startswith(prefix)]:
            self._data.pop(k, None)


class Cache:
    def __init__(self) -> None:
        self._memory = _MemoryCache()
        self._redis = None
        self.backend = "memory"

    async def connect(self) -> str:
        if not settings.redis_url:
            return self.backend
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(
                settings.redis_url, encoding="utf-8", decode_responses=True
            )
            await client.ping()
            self._redis = client
            self.backend = "redis"
            log.info("redis connected")
        except Exception as exc:  # noqa: BLE001
            log.warning("redis unavailable (%s); using in-process cache", exc)
        return self.backend

    async def close(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._redis = None

    async def get(self, key: str) -> Optional[str]:
        if self._redis is not None:
            try:
                return await self._redis.get(key)
            except Exception:  # noqa: BLE001
                pass
        return self._memory.get(key)

    async def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        if self._redis is not None:
            try:
                await self._redis.set(key, value, ex=ttl)
                return
            except Exception:  # noqa: BLE001
                pass
        self._memory.set(key, value, ttl)

    async def delete(self, key: str) -> None:
        if self._redis is not None:
            try:
                await self._redis.delete(key)
            except Exception:  # noqa: BLE001
                pass
        self._memory.delete(key)

    async def get_json(self, key: str) -> Optional[Any]:
        raw = await self.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    async def set_json(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        try:
            await self.set(key, json.dumps(value, default=str), ttl)
        except (TypeError, ValueError) as exc:  # noqa: BLE001
            log.debug("cache serialise failed for %s: %s", key, exc)


cache = Cache()
