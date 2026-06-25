"""
config_client.py
Reads the per-source identity/pool/version map from the Config API (R13c).

`GET /config/quality_ingest/source_map?tenant_id=<t>` resolves tenant override →
global default. The value is one JSON keyed by `source`:

    {
      "ccaas:genesys": {
        "pools":  {"Genesys-Queue-42": "retencao_humano"},
        "agents": {
          "agent-007": {"kind": "human", "user_id": "wang@opuscom.com.br"},
          "bot-ai-1":  {"kind": "ai", "skill_id": "skill_retencao_v1", "deploy_version": "v3"}
        }
      }
    }

Graceful by design: any error / 404 / missing key → empty map → the mapper falls
back to pass-through (the event's own pool_id/skill_id/external_agent_id). A short
in-process TTL cache per tenant avoids a Config API round-trip per request.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("plughub.quality_ingest.config_client")


class SourceMapClient:
    def __init__(self, config_api_url: str, *, cache_ttl_s: int = 60) -> None:
        self._base = config_api_url.rstrip("/")
        self._ttl = cache_ttl_s
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def get_source_map(self, tenant_id: str) -> dict[str, Any]:
        """Return the source_map for a tenant ({} when unset/unavailable)."""
        hit = self._cache.get(tenant_id)
        if hit and hit[0] > time.monotonic():
            return hit[1]

        value: dict[str, Any] = {}
        url = f"{self._base}/config/quality_ingest/source_map"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url, params={"tenant_id": tenant_id})
            if resp.status_code == 200:
                body = resp.json()
                v = body.get("value")
                if isinstance(v, dict):
                    value = v
            elif resp.status_code != 404:
                logger.warning("source_map fetch %s → %s", url, resp.status_code)
        except Exception as exc:  # noqa: BLE001 — graceful: pass-through on any error
            logger.warning("source_map fetch failed (%s) — pass-through: %s", url, exc)

        self._cache[tenant_id] = (time.monotonic() + self._ttl, value)
        return value
