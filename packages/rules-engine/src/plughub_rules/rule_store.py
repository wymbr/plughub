"""
rule_store.py
Loading and caching of active rules per tenant.
"""

from __future__ import annotations
import json
import time
from typing import Any

import redis.asyncio as aioredis

from .models import Rule
from .config import get_settings

_RULES_KEY = lambda tenant_id: f"rules:{tenant_id}:active"


class RuleStore:
    """
    Loads active tenant rules from Redis.
    Local cache with TTL to avoid repeated reads.
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis    = redis_client
        self._settings = get_settings()
        self._cache:   dict[str, tuple[list[Rule], float]] = {}

    async def get_active_rules(self, tenant_id: str) -> list[Rule]:
        """Returns active tenant rules. Cache TTL: 60s."""
        cached = self._cache.get(tenant_id)
        if cached and (time.monotonic() - cached[1]) < self._settings.rule_cache_ttl_seconds:
            return cached[0]

        raw = await self._redis.get(_RULES_KEY(tenant_id))
        if not raw:
            return []

        try:
            rules_data: list[dict[str, Any]] = json.loads(raw)
            rules = [
                Rule.model_validate(r) for r in rules_data
                if r.get("status") in ("active", "shadow")
            ]
        except Exception:
            return []

        self._cache[tenant_id] = (rules, time.monotonic())
        return rules

    async def save_rules(self, tenant_id: str, rules: list[Rule]) -> None:
        """Persists rules to Redis (called by Agent Registry when registering a rule)."""
        await self._redis.set(
            _RULES_KEY(tenant_id),
            json.dumps([r.model_dump() for r in rules]),
        )
        # Invalidate cache
        self._cache.pop(tenant_id, None)
