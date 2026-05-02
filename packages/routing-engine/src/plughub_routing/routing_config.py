"""
routing_config.py
Local cache for Config API routing namespace settings.

Fetches config from the Config API at startup and caches it in memory.
Invalidated via config.changed Kafka events (namespace == "routing").
After invalidation, a background reload fetches fresh values from the API.

Defaults mirror the seeds in packages/config-api/src/plughub_config_api/seed.py
so the routing-engine works correctly even when the Config API is unreachable.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("plughub.routing.routing_config")

# Defaults matching Config API seed — routing namespace
_DEFAULTS: dict[str, Any] = {
    "snapshot_ttl_s":           120,
    "sla_default_ms":           480_000,
    "estimated_wait_factor":    0.7,
    "congestion_sla_factor":    1.5,
    "performance_score_weight": 0.0,
    "score_weights": {
        "sla":        0.4,
        "channel":    0.3,
        "skills":     0.2,
        "load":       0.1,
    },
}


class RoutingConfigCache:
    """
    In-memory cache of Config API routing namespace.

    Thread-safe for asyncio (single-threaded event loop).
    Uses httpx.AsyncClient for non-blocking HTTP.

    Usage:
        cache = RoutingConfigCache()
        await cache.reload(config_api_url, http_client)    # startup
        value = cache.get("performance_score_weight", 0.0) # per routing call
        cache.invalidate()                                  # on config.changed
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
        Safe to call synchronously from the routing hot path.
        """
        if key in self._data:
            return self._data[key]
        return _DEFAULTS.get(key, default)

    def invalidate(self) -> None:
        """
        Marks cache as stale.  Called by ConfigChangedHandler on
        config.changed events with namespace == "routing".
        Does NOT clear _data — existing values remain available until
        a reload completes so routing continues during the refresh.
        """
        self._invalidated = True
        logger.debug("RoutingConfigCache invalidated")

    @property
    def is_stale(self) -> bool:
        return self._invalidated

    async def reload(
        self,
        config_api_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        """
        Fetches GET {config_api_url}/config/routing and populates the cache.
        Falls back silently to defaults on any error so the routing-engine
        remains operational when the Config API is temporarily unreachable.
        """
        url = f"{config_api_url.rstrip('/')}/config/routing"
        try:
            resp = await http_client.get(url, timeout=5.0)
            resp.raise_for_status()
            body = resp.json()
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
                "RoutingConfigCache reloaded: %d keys from %s",
                len(new_data), url,
            )
        except Exception as exc:
            # Degraded mode: keep whatever was cached (or defaults) and re-mark
            # as stale so the next config.changed event will trigger another reload.
            self._invalidated = True
            logger.warning(
                "RoutingConfigCache reload failed (%s) — using cached/default values",
                exc,
            )


# Module-level singleton — imported by kafka_listener and router
routing_config = RoutingConfigCache()
