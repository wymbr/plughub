"""
cache.py
Semantic cache for LLM responses per tenant.
Spec: PlugHub v24.0 section 2.2a

Key: {tenant_id}:cache:{sha256(normalised_prompt)[:32]}
TTL: 5 minutes (300s)
Invalidation: per tenant_id via scan+del.
"""

from __future__ import annotations
import hashlib
import json
from typing import Any


class SemanticCache:
    """
    LLM response cache keyed on a hash of the normalised prompt.
    Default TTL of 5 minutes for cost and latency reduction.
    """

    def __init__(self, redis: Any, ttl_seconds: int = 300) -> None:
        self._redis = redis
        self._ttl   = ttl_seconds

    def _hash(self, messages: list[dict]) -> str:
        """SHA-256 hash of normalised messages (sort_keys for determinism)."""
        normalized = json.dumps(messages, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(normalized.encode()).hexdigest()[:32]

    def _key(self, tenant_id: str, prompt_hash: str) -> str:
        return f"{tenant_id}:cache:{prompt_hash}"

    async def get(self, tenant_id: str, messages: list[dict]) -> dict[str, Any] | None:
        """Returns cached response or None if not found."""
        key = self._key(tenant_id, self._hash(messages))
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set(self, tenant_id: str, messages: list[dict], response: dict[str, Any]) -> None:
        """Saves response to cache with the configured TTL."""
        key = self._key(tenant_id, self._hash(messages))
        await self._redis.set(key, json.dumps(response), ex=self._ttl)

    async def invalidate_tenant(self, tenant_id: str) -> None:
        """Removes all cache entries for a tenant (via SCAN)."""
        pattern = f"{tenant_id}:cache:*"
        cursor   = 0
        while True:
            cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
            if keys:
                await self._redis.delete(*keys)
            if cursor == 0:
                break
