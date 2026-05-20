"""
endpoint_resolver.py
Resolves channel identifiers to pool_ids by querying the agent-registry
channel-endpoints API with a short in-process TTL cache.

Layer 2 of the channel-gateway routing stack:

  URL path slug / phone number / DID
      ↓  (this module)
  GET /v1/channel-endpoints?channel={ch}&identifier={id}&active=true
      ↓
  pool_id  (or None → caller applies backward-compat fallback)

The cache prevents repeated HTTP calls on the hot WebSocket connect path
while still reflecting configuration changes within `endpoint_cache_ttl_s`
seconds (default 30 s).

Cache invalidation note
-----------------------
The `invalidate()` helper is exposed so that a future registry.changed Kafka
consumer can drop stale entries immediately.  Until that consumer is wired,
the TTL-based expiry is the only invalidation mechanism.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger("plughub.channel-gateway.endpoint-resolver")

# ─── In-process cache ────────────────────────────────────────────────────────
# Key:   (tenant_id, channel, identifier)
# Value: (resolved_pool_id | None, expires_at_monotonic_seconds)
#
# Storing None means "we looked and found nothing" — avoids hammering the
# registry for unknown identifiers on every connection attempt.

_CacheKey   = tuple[str, str, str]
_CacheValue = tuple[Optional[str], float]

_cache: dict[_CacheKey, _CacheValue] = {}
_lock  = asyncio.Lock()


# ─── Public API ──────────────────────────────────────────────────────────────

async def resolve_pool(
    *,
    channel:            str,
    identifier:         str,
    tenant_id:          str,
    agent_registry_url: str,
    cache_ttl_s:        int   = 30,
    http_timeout_s:     float = 2.0,
) -> Optional[str]:
    """
    Return the pool_id mapped to (channel, identifier) for the given tenant,
    or None if no active record exists or the registry cannot be reached.

    The caller is responsible for applying a fallback when None is returned
    (typically: treat the identifier itself as the pool_id for backward compat).

    Parameters
    ----------
    channel:            Channel type, e.g. "webchat", "whatsapp".
    identifier:         The external identifier — webchat slug, WhatsApp DID, etc.
    tenant_id:          Tenant to scope the lookup.
    agent_registry_url: Base URL of the agent-registry service.
    cache_ttl_s:        How long (seconds) to cache a positive or negative result.
    http_timeout_s:     HTTP connect+read timeout; kept low to avoid blocking WS.
    """
    cache_key: _CacheKey = (tenant_id, channel, identifier)
    now = time.monotonic()

    # ── Fast path: cache hit (lock-free read) ────────────────────────────────
    entry = _cache.get(cache_key)
    if entry is not None:
        pool_id, expires_at = entry
        if now < expires_at:
            return pool_id

    # ── Slow path: refresh under lock (prevents stampede) ───────────────────
    async with _lock:
        # Double-check: another coroutine may have already refreshed while we
        # were waiting for the lock.
        entry = _cache.get(cache_key)
        if entry is not None:
            pool_id, expires_at = entry
            if now < expires_at:
                return pool_id

        pool_id = await _fetch_pool(
            channel            = channel,
            identifier         = identifier,
            tenant_id          = tenant_id,
            agent_registry_url = agent_registry_url,
            http_timeout_s     = http_timeout_s,
        )

        _cache[cache_key] = (pool_id, time.monotonic() + cache_ttl_s)
        return pool_id


def invalidate(*, tenant_id: str, channel: Optional[str] = None) -> None:
    """
    Evict cache entries for a tenant, optionally scoped to a channel.

    Call this when a registry.changed event is received so that the next
    connection attempt immediately picks up the new mapping.
    """
    to_drop = [
        k for k in _cache
        if k[0] == tenant_id and (channel is None or k[1] == channel)
    ]
    for k in to_drop:
        _cache.pop(k, None)

    if to_drop:
        logger.debug(
            "endpoint-resolver: invalidated %d cache entries (tenant=%s channel=%s)",
            len(to_drop), tenant_id, channel or "*",
        )


# ─── Internal helpers ─────────────────────────────────────────────────────────

async def _fetch_pool(
    *,
    channel:            str,
    identifier:         str,
    tenant_id:          str,
    agent_registry_url: str,
    http_timeout_s:     float,
) -> Optional[str]:
    """
    Perform a single HTTP GET to agent-registry and return the first matching
    active pool_id, or None on any error / no match.
    """
    url = f"{agent_registry_url.rstrip('/')}/v1/channel-endpoints"
    params = {
        "channel":    channel,
        "identifier": identifier,
        "active":     "true",
    }
    headers = {"X-Tenant-Id": tenant_id}

    try:
        async with httpx.AsyncClient(timeout=http_timeout_s) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "endpoint-resolver: registry returned HTTP %s "
            "(channel=%s identifier=%s tenant=%s)",
            exc.response.status_code, channel, identifier, tenant_id,
        )
        return None
    except Exception as exc:
        logger.warning(
            "endpoint-resolver: could not reach agent-registry "
            "(channel=%s identifier=%s tenant=%s): %s",
            channel, identifier, tenant_id, exc,
        )
        return None

    endpoints: list[dict] = data.get("endpoints", [])
    if not endpoints:
        logger.debug(
            "endpoint-resolver: no active endpoint (channel=%s identifier=%s tenant=%s)",
            channel, identifier, tenant_id,
        )
        return None

    pool_id: str = endpoints[0]["pool_id"]
    logger.info(
        "endpoint-resolver: %s/%s → pool=%s (tenant=%s)",
        channel, identifier, pool_id, tenant_id,
    )
    return pool_id
