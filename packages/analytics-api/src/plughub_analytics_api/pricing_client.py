"""
pricing_client.py
Cliente mínimo do pricing-api (Fase 2 — Pools/Infra report).

Busca a capacidade configurada (contratada) do tenant —
GET {PLUGHUB_PRICING_API_URL}/v1/pricing/capacity/{tenant_id} — usada como
denominador do TOTAL em /reports/pools/occupancy (decisão 2026-06-04:
per-pool continua com a capacidade provisionada flashada pelo sampler;
só o teto do total vem do pricing).

Degradação graciosa: URL vazia, erro HTTP ou timeout → retorna None e o
relatório mantém a capacidade provisionada. Cache TTL curto em memória para
não bater no pricing a cada render da aba.
"""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger("plughub.analytics.pricing_client")

_TTL_S = 60.0
_cache: dict[str, tuple[float, int | None]] = {}   # tenant_id -> (expires_at, value)


async def get_configured_agent_capacity(base_url: str, tenant_id: str) -> int | None:
    """Capacidade configurada total de agentes (ai_agent + human_agent) ou None."""
    if not base_url:
        return None

    hit = _cache.get(tenant_id)
    if hit and hit[0] > time.monotonic():
        return hit[1]

    value: int | None = None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/v1/pricing/capacity/{tenant_id}")
            resp.raise_for_status()
            raw = resp.json().get("agent_capacity_total")
            value = int(raw) if raw is not None else None
            if value is not None and value <= 0:
                value = None   # sem recursos configurados → sem teto do pricing
    except Exception as exc:
        logger.warning("pricing capacity unavailable tenant=%s: %s", tenant_id, exc)
        # cache negativo curto também — evita marteladas quando o pricing está fora

    _cache[tenant_id] = (time.monotonic() + _TTL_S, value)
    return value
