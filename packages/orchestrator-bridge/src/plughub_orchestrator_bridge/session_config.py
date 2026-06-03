"""
session_config.py
Local cache for Config API session namespace settings.

Fetches config from the Config API at startup and caches in memory.
Invalidated via config.changed Kafka events (namespace == "session").
After invalidation, a background reload fetches fresh values from the API.

Defaults mirror the seeds in packages/config-api/src/plughub_config_api/seed.py
so the orchestrator-bridge works correctly even when the Config API is unreachable.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

logger = logging.getLogger("plughub.orchestrator-bridge.session_config")

# Defaults matching Config API seed — session namespace
_DEFAULTS: dict[str, Any] = {
    "orchestrator_session_ttl_s":   14_400,   # 4h — session Redis keys in bridge
    "transcript_ttl_s":             14_400,   # 4h — conversation-writer Redis buffer
    "replayer_hydration_ttl_s":      3_600,   # 1h — stream hydration for evaluation
    "replay_context_ttl_s":          3_600,   # 1h — ReplayContext key TTL
    "pool_config_ttl_s":             3_600,   # 1h — pool config cache TTL
    "sentiment_live_ttl_s":            300,   # 5m — live sentiment in Redis
    # Fase C (queue-attended-model): tenant default queue-treatment agent —
    # used by process_queued when the pool has no queue_config. "" = disabled.
    "queue_default_agent_type_id":      "",
    "queue_default_skill_id":           "",
}


class SessionConfigCache:
    """
    In-memory cache of Config API session namespace.

    Thread-safe for asyncio (single-threaded event loop).
    Uses aiohttp.ClientSession for non-blocking HTTP (same client as bridge).

    Usage:
        cache = SessionConfigCache()
        await cache.reload(config_api_url, http_client)         # startup
        ttl = cache.get("orchestrator_session_ttl_s", 14400)   # per Redis call
        cache.invalidate()                                       # on config.changed
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._loaded_at: float = 0.0
        self._invalidated: bool = True   # start invalid — forces first reload

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """
        Returns value from cache, falling back to _DEFAULTS, then `default`.
        Safe to call synchronously from the event-handling hot path.
        """
        if key in self._data:
            return self._data[key]
        return _DEFAULTS.get(key, default)

    def invalidate(self) -> None:
        """
        Marks cache as stale. Called by _handle_config_changed on
        config.changed events with namespace == "session".
        Does NOT clear _data — existing values remain available until
        a reload completes so the bridge continues operating during refresh.
        """
        self._invalidated = True
        logger.debug("SessionConfigCache invalidated")

    @property
    def is_stale(self) -> bool:
        return self._invalidated

    async def reload(
        self,
        config_api_url: str,
        http: aiohttp.ClientSession,
    ) -> None:
        """
        Fetches GET {config_api_url}/config/session and populates the cache.
        Falls back silently to defaults on any error so the bridge
        remains operational when the Config API is temporarily unreachable.
        """
        url = f"{config_api_url.rstrip('/')}/config/session"
        try:
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    # Config API returns { "entries": { key: { "value": ..., ... } } }
                    # or a flat { key: value } dict depending on the endpoint used.
                    entries = body.get("entries") or body
                    new_data: dict[str, Any] = {}
                    for k, v in entries.items():
                        # Unwrap ConfigEntry envelope if present
                        if isinstance(v, dict) and "value" in v:
                            new_data[k] = v["value"]
                        else:
                            new_data[k] = v
                    self._data = new_data
                    self._loaded_at = time.monotonic()
                    self._invalidated = False
                    logger.info(
                        "SessionConfigCache reloaded: %d keys from %s",
                        len(new_data), url,
                    )
                else:
                    logger.warning(
                        "SessionConfigCache: Config API returned HTTP %d — using cached/default values",
                        resp.status,
                    )
                    self._invalidated = True
        except Exception as exc:
            # Degraded mode: keep whatever was cached (or defaults) and re-mark
            # as stale so the next config.changed event triggers another reload.
            self._invalidated = True
            logger.warning(
                "SessionConfigCache reload failed (%s) — using cached/default values", exc
            )


# Module-level singleton — imported by main.py
session_config = SessionConfigCache()
