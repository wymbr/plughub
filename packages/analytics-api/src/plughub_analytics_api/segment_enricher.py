"""
segment_enricher.py
Post-hoc segment_id enrichment for Kafka events that lack it.

Two entry-points:
  lookup_by_instance(session_id, instance_id, tenant_id)
    → Used for mcp.audit events: the AuditRecord carries instance_id (the
      agent that invoked the MCP tool).  Redis key:
        session:{session_id}:segment:{instance_id}

  lookup_primary(session_id, tenant_id)
    → Used for sentiment.updated events: the AI Gateway emits one score per
      LLM turn and does not carry instance_id.  We look for the current
      primary participant's segment.  Redis key:
        session:{session_id}:primary_segment

Lookup chain (same for both):
  1. In-memory LRU-style cache   — zero I/O, bounded at MAX_CACHE_SIZE entries
  2. Redis                       — sub-millisecond, valid while session is live
  3. ClickHouse FINAL query      — fallback for sessions whose Redis keys have
                                    expired (TTL 4 h); returns the most recent
                                    matching segment row

If all three fail the caller receives None.  The row is then written with
segment_id = None (or "") which is accepted by every table that holds the
column (sentiment_events: Nullable(String); session_timeline: String defaults
to "").  Unenriched rows can be back-filled later by re-consuming from the
Kafka topic if the ClickHouse data is available.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .clickhouse import AnalyticsStore

logger = logging.getLogger("plughub.analytics.segment_enricher")

# Caps the number of (session_id, lookup_key) → segment_id entries kept in
# process memory.  When the dict exceeds this size we evict the oldest half
# (simple FIFO — no heavy OrderedDict gymnastics needed here).
_MAX_CACHE_SIZE = 10_000


class SegmentEnricher:
    """
    Resolves segment_id from (session_id, participant_id) pairs.

    Thread-safety: designed for use inside a single asyncio task (the Kafka
    consumer) — no locking required.
    """

    def __init__(self, redis: object, store: "AnalyticsStore") -> None:
        self._redis = redis  # aioredis client
        self._store = store
        # Keyed by (session_id, lookup_key) where lookup_key is either
        # an instance_id string or the sentinel "__primary__".
        self._cache: dict[tuple[str, str], str] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    async def lookup_by_instance(
        self,
        session_id:  str,
        instance_id: str,
        tenant_id:   str,
    ) -> str | None:
        """
        Return the segment_id for the given agent instance inside a session.
        Used to enrich mcp.audit events.
        """
        if not session_id or not instance_id:
            return None
        cache_key = (session_id, instance_id)
        hit = self._cache.get(cache_key)
        if hit:
            return hit

        # Redis
        redis_key = f"session:{session_id}:segment:{instance_id}"
        try:
            val = await self._redis.get(redis_key)  # type: ignore[attr-defined]
            if val:
                self._put_cache(cache_key, val)
                return val
        except Exception as exc:
            logger.debug("Redis lookup failed for %s: %s", redis_key, exc)

        # ClickHouse fallback
        try:
            val = await self._store.lookup_segment_id(tenant_id, session_id, instance_id)
            if val:
                self._put_cache(cache_key, val)
                return val
        except Exception as exc:
            logger.debug(
                "ClickHouse segment lookup failed session=%s instance=%s: %s",
                session_id, instance_id, exc,
            )

        return None

    async def lookup_primary(
        self,
        session_id: str,
        tenant_id:  str,
    ) -> str | None:
        """
        Return the segment_id of the current primary participant for a session.
        Used to enrich sentiment.updated events which carry no instance_id.
        """
        if not session_id:
            return None
        cache_key = (session_id, "__primary__")
        hit = self._cache.get(cache_key)
        if hit:
            return hit

        # Redis — orchestrator-bridge stores the primary segment UUID here
        redis_key = f"session:{session_id}:primary_segment"
        try:
            val = await self._redis.get(redis_key)  # type: ignore[attr-defined]
            if val:
                self._put_cache(cache_key, val)
                return val
        except Exception as exc:
            logger.debug("Redis lookup failed for %s: %s", redis_key, exc)

        # ClickHouse fallback — find the most recent primary segment
        try:
            val = await self._store.lookup_primary_segment_id(tenant_id, session_id)
            if val:
                self._put_cache(cache_key, val)
                return val
        except Exception as exc:
            logger.debug(
                "ClickHouse primary segment lookup failed session=%s: %s",
                session_id, exc,
            )

        return None

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _put_cache(self, key: tuple[str, str], value: str) -> None:
        if len(self._cache) >= _MAX_CACHE_SIZE:
            # Evict the oldest half via FIFO (dict insertion order, Python 3.7+)
            evict_count = _MAX_CACHE_SIZE // 2
            for k in list(self._cache.keys())[:evict_count]:
                del self._cache[k]
        self._cache[key] = value
