"""
webchat_config.py
HTTP-backed in-process cache for the Config API 'webchat' namespace.

Part of the config-http-propagation arc. Replaces direct-Redis reads of config
values: the Config API only maintains the TTL cache key `plughub:cfg:...`
(invalidated on write, lazily populated on API read), and the durable
`{tenant}:config:...` key the old resolvers read was NEVER written — so direct
Redis reads always fell back to the in-code default ("config-api vence" never
actually took effect).

Mirrors the canonical pattern used by orchestrator-bridge SessionConfigCache and
routing-engine RoutingConfigCache: fetch via the Config API HTTP endpoint
(GET /config/webchat?tenant_id=...), cache in-process, invalidate on
config.changed Kafka events (namespace == "webchat"), reload in background.

Defaults mirror packages/config-api/src/plughub_config_api/seed.py (webchat
namespace) so the channel-gateway works even when the Config API is unreachable.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("plughub.channel-gateway.webchat_config")

# Defaults matching Config API seed — webchat namespace.
_DEFAULTS: dict[str, Any] = {
    "auth_timeout_s":         30,   # WS handshake timeout (webchat + webrtc)
    "attachment_expiry_days": 30,   # soft-delete stage-1 expiry
}


class WebchatConfigCache:
    """
    In-memory cache of the Config API webchat namespace for a single tenant
    (the deployment's settings.tenant_id).

    Usage:
        webchat_config = WebchatConfigCache()
        await webchat_config.reload(config_api_url, tenant_id)   # startup
        webchat_config.get("auth_timeout_s", 30)                 # hot path (sync)
        webchat_config.invalidate()                              # on config.changed
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._loaded_at: float = 0.0
        self._invalidated: bool = True   # start invalid — forces first reload

    def get(self, key: str, default: Any = None) -> Any:
        """Returns value from cache, falling back to _DEFAULTS, then `default`."""
        if key in self._data:
            return self._data[key]
        return _DEFAULTS.get(key, default)

    def invalidate(self) -> None:
        """Marks cache stale (called on config.changed namespace == 'webchat')."""
        self._invalidated = True
        logger.debug("WebchatConfigCache invalidated")

    @property
    def is_stale(self) -> bool:
        return self._invalidated

    async def reload(self, config_api_url: str, tenant_id: str) -> None:
        """
        Fetches GET {config_api_url}/config/webchat?tenant_id=... and populates the
        cache. Falls back silently to cached/default values on any error so the
        gateway stays operational when the Config API is temporarily unreachable.
        """
        url = f"{config_api_url.rstrip('/')}/config/webchat"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, params={"tenant_id": tenant_id})
            if resp.status_code == 200:
                body = resp.json()
                # Config API returns { "entries": { key: { "value": ..., ... } } }
                # or a flat { key: value } dict depending on the endpoint.
                entries = body.get("entries") or body
                new_data: dict[str, Any] = {}
                for k, v in entries.items():
                    new_data[k] = v["value"] if isinstance(v, dict) and "value" in v else v
                self._data = new_data
                self._loaded_at = time.monotonic()
                self._invalidated = False
                logger.info("WebchatConfigCache reloaded: %d keys from %s", len(new_data), url)
            else:
                self._invalidated = True
                logger.warning(
                    "WebchatConfigCache: Config API returned HTTP %d — using cached/default values",
                    resp.status_code,
                )
        except Exception as exc:
            self._invalidated = True
            logger.warning(
                "WebchatConfigCache reload failed (%s) — using cached/default values", exc
            )


# Module-level singleton — imported by main.py (startup reload + config.changed)
# and by attachment_store resolvers (hot-path reads).
webchat_config = WebchatConfigCache()
