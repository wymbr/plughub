"""
rate_limit.py
Rate limiting per tenant_id and agent_type_id via Redis.
Spec: PlugHub v24.0 section 2.2a

Redis key: {tenant_id}:ratelimit:{agent_type_id}:{window_minute}
Sliding window: current minute (unix_timestamp // 60).
"""

from __future__ import annotations
import time
from typing import Any


class RateLimitExceeded(Exception):
    """Raised when the per-minute call limit has been exceeded."""
    def __init__(self, tenant_id: str, agent_type_id: str, limit: int) -> None:
        super().__init__(
            f"Rate limit exceeded: tenant={tenant_id} agent={agent_type_id} limit={limit}/min"
        )
        self.tenant_id     = tenant_id
        self.agent_type_id = agent_type_id
        self.limit         = limit


class RateLimiter:
    """
    Limits AI Gateway calls per (tenant_id, agent_type_id) per minute.
    Implemented with INCR + EXPIRE in Redis.
    """

    def __init__(self, redis: Any, limit_per_minute: int) -> None:
        self._redis = redis
        self._limit = limit_per_minute

    def _key(self, tenant_id: str, agent_type_id: str) -> str:
        window = int(time.time() // 60)
        return f"{tenant_id}:ratelimit:{agent_type_id}:{window}"

    async def check_and_increment(self, tenant_id: str, agent_type_id: str) -> None:
        """
        Increments the counter and checks the limit.
        Raises RateLimitExceeded if the limit has been reached.
        """
        key   = self._key(tenant_id, agent_type_id)
        count = await self._redis.incr(key)
        if count == 1:
            # First call in the window — set 60s TTL
            await self._redis.expire(key, 60)
        if count > self._limit:
            raise RateLimitExceeded(tenant_id, agent_type_id, self._limit)
