"""
config_client.py
Cliente mínimo do config-api (R8b — tuning de calibração em tempo de request).

Lê um valor do namespace horizontal `evaluation` (limiar de divergência, N mínimo)
— GET {PLUGHUB_CONFIG_API_URL}/config/{namespace}?tenant_id={tenant_id} — resolvendo
override do tenant → default global.

Degradação graciosa: URL vazia, erro HTTP/timeout ou chave ausente → retorna o
`default` do chamador (o código nunca fica sem valor). Cache em memória TTL curto por
(tenant, namespace) para não bater no config-api a cada render do dashboard.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("plughub.analytics.config_client")

_TTL_S = 60.0
# (tenant, namespace) -> (expires_at, entries dict)
_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


async def _fetch_namespace(base_url: str, tenant_id: str, namespace: str) -> dict[str, Any]:
    """Busca o namespace inteiro (cacheado). {} em qualquer falha."""
    key = (tenant_id, namespace)
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]

    entries: dict[str, Any] = {}
    try:
        url = f"{base_url.rstrip('/')}/config/{namespace}?tenant_id={tenant_id}"
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            body = resp.json()
            entries = body.get("entries") or body or {}
    except Exception as exc:
        logger.warning(
            "config %s unavailable tenant=%s: %s", namespace, tenant_id, exc
        )

    _cache[key] = (time.monotonic() + _TTL_S, entries)
    return entries


def _coerce(raw: Any, default: Any) -> Any:
    """Mantém o tipo do default (float/int/bool/str)."""
    if raw is None:
        return default
    try:
        if isinstance(default, bool):
            return bool(raw)
        if isinstance(default, int) and not isinstance(default, bool):
            return int(raw)
        if isinstance(default, float):
            return float(raw)
    except (TypeError, ValueError):
        return default
    return raw


async def get_config_value(
    base_url: str, tenant_id: str, namespace: str, key: str, default: Any,
) -> Any:
    """Valor de config (override tenant → global → default). Nunca lança."""
    if not base_url:
        return default
    entries = await _fetch_namespace(base_url, tenant_id, namespace)
    entry = entries.get(key)
    raw = entry.get("value") if isinstance(entry, dict) else entry
    return _coerce(raw, default)
