"""
cache.py
Redis cache layer for config lookups (TTL 60s by default).

Key scheme:
  plughub:cfg:{tenant_id}:{namespace}:{key}  → JSON-encoded resolved value
  plughub:cfg:ns:{tenant_id}:{namespace}      → JSON-encoded {key: value} dict

Invalidation rules:
  - Writing tenant-specific (t_id, ns, k):
      delete plughub:cfg:{t_id}:{ns}:{k}
      delete plughub:cfg:ns:{t_id}:{ns}
  - Writing global ('__global__', ns, k):
      delete plughub:cfg:__global__:{ns}:{k}
      delete plughub:cfg:ns:__global__:{ns}
      scan + delete plughub:cfg:*:{ns}:{k}   (tenant overrides may be stale)
      scan + delete plughub:cfg:ns:*:{ns}    (namespace caches may be stale)

The scan on global write is a best-effort invalidation.
Any remaining tenant caches expire naturally within TTL (60s).
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("plughub.config.cache")

_PREFIX = "plughub:cfg"


def _key(tenant_id: str, namespace: str, key: str) -> str:
    return f"{_PREFIX}:{tenant_id}:{namespace}:{key}"


def _ns_key(tenant_id: str, namespace: str) -> str:
    return f"{_PREFIX}:ns:{tenant_id}:{namespace}"


class ConfigCache:
    """Thin wrapper around a redis.asyncio client for config caching."""

    def __init__(self, redis: Any, ttl: int = 60) -> None:
        self._r   = redis
        self._ttl = ttl

    # ── read ─────────────────────────────────────────────────────────────────

    async def get(self, tenant_id: str, namespace: str, key: str) -> Any:
        """Returns cached resolved value, or _MISS sentinel."""
        try:
            raw = await self._r.get(_key(tenant_id, namespace, key))
            if raw is None:
                return _MISS
            return json.loads(raw)
        except Exception as exc:
            logger.warning("cache.get failed: %s", exc)
            return _MISS

    async def get_namespace(self, tenant_id: str, namespace: str) -> Any:
        """Returns cached namespace dict, or _MISS."""
        try:
            raw = await self._r.get(_ns_key(tenant_id, namespace))
            if raw is None:
                return _MISS
            return json.loads(raw)
        except Exception as exc:
            logger.warning("cache.get_namespace failed: %s", exc)
            return _MISS

    # ── write ─────────────────────────────────────────────────────────────────

    async def set(self, tenant_id: str, namespace: str, key: str, value: Any) -> None:
        try:
            await self._r.set(
                _key(tenant_id, namespace, key),
                json.dumps(value),
                ex=self._ttl,
            )
        except Exception as exc:
            logger.warning("cache.set failed: %s", exc)

    async def set_namespace(self, tenant_id: str, namespace: str, data: dict) -> None:
        try:
            await self._r.set(
                _ns_key(tenant_id, namespace),
                json.dumps(data),
                ex=self._ttl,
            )
        except Exception as exc:
            logger.warning("cache.set_namespace failed: %s", exc)

    # ── invalidation ──────────────────────────────────────────────────────────

    async def invalidate(self, tenant_id: str, namespace: str, key: str) -> None:
        """Invalidates cache entries after a write."""
        try:
            # always delete the specific key and namespace cache for this tenant
            await self._r.delete(
                _key(tenant_id, namespace, key),
                _ns_key(tenant_id, namespace),
            )
            # if writing global, also invalidate all tenant variants of this key
            from .db import GLOBAL
            if tenant_id == GLOBAL:
                await self._scan_delete(f"{_PREFIX}:*:{namespace}:{key}")
                await self._scan_delete(f"{_PREFIX}:ns:*:{namespace}")
        except Exception as exc:
            logger.warning("cache.invalidate failed: %s", exc)

    async def _scan_delete(self, pattern: str) -> None:
        """SCAN + DELETE for a key pattern (best-effort)."""
        try:
            cursor = 0
            while True:
                cursor, keys = await self._r.scan(cursor, match=pattern, count=100)
                if keys:
                    await self._r.delete(*keys)
                if cursor == 0:
                    break
        except Exception as exc:
            logger.warning("cache._scan_delete failed pattern=%s: %s", pattern, exc)


# Sentinel object for cache misses (distinct from None, which is a valid cached value)
class _MissSentinel:
    def __repr__(self) -> str:
        return "MISS"


_MISS = _MissSentinel()
MISS  = _MISS  # exported alias for type-checking in store.py
