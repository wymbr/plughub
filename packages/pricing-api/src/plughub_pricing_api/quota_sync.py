"""
quota_sync.py
Capacity-governance item 1 — pricing escreve a quota de admissão no Redis.

A cada mutação de resources (upsert / delete / activate / deactivate de reserva)
recalcula C do tenant — capacidade contratada de agentes (ai_agent + human_agent,
base + reservas comerciais ativas, todas as instalações) — e grava:

    {tenant_id}:quota:max_concurrent_sessions = C

Leitores existentes (o gate já estava pronto, faltava o produtor):
  - routing-engine `AdmissionController._shared_limit` (admissão híbrida:
    shared = C − Σ session_reservation; rejeição = outage `shared_full`/`quota`)
  - mcp-server `checkConcurrentSessions`

Semântica:
  - C > 0  → SET (recompute completo, idempotente)
  - C == 0 (nenhum resource configurado) → DEL → sem limite (comportamento
    anterior preservado para instalações sem pricing configurado)
  - Redis indisponível / url vazia → loga e segue (billing nunca quebra por quota)

No boot, `sync_all` re-deriva a quota de todos os tenants com resources —
auto-cura após flush do Redis.
"""
from __future__ import annotations

import logging

import asyncpg
import redis.asyncio as aioredis

from . import db as pricing_db

logger = logging.getLogger("plughub.pricing.quota_sync")

_QUOTA_KEY = "{tenant_id}:quota:max_concurrent_sessions"


async def sync_tenant(
    redis_client: "aioredis.Redis | None",
    pg_pool:      asyncpg.Pool,
    tenant_id:    str,
) -> int | None:
    """Recalcula C do tenant e grava/remove a quota. Retorna C (None se sync off/erro)."""
    if redis_client is None:
        return None
    try:
        cap = await pricing_db.get_capacity(pg_pool, tenant_id, installation_id=None)
        total = int(cap.get("agent_capacity_total") or 0)
        key = _QUOTA_KEY.format(tenant_id=tenant_id)
        if total > 0:
            await redis_client.set(key, str(total))
            logger.info("quota sync: %s = %d", key, total)
        else:
            await redis_client.delete(key)
            logger.info("quota sync: %s removed (no configured agent capacity)", key)
        return total
    except Exception as exc:
        logger.warning("quota sync failed tenant=%s — %s", tenant_id, exc)
        return None


async def sync_all(redis_client: "aioredis.Redis | None", pg_pool: asyncpg.Pool) -> None:
    """Boot: re-deriva a quota de todos os tenants com resources (cura Redis flush)."""
    if redis_client is None:
        logger.info("quota sync disabled (PLUGHUB_PRICING_REDIS_URL not set)")
        return
    try:
        rows = await pg_pool.fetch(
            "SELECT DISTINCT tenant_id FROM pricing.installation_resources"
        )
        for r in rows:
            await sync_tenant(redis_client, pg_pool, r["tenant_id"])
        logger.info("quota sync: bootstrap done (%d tenant(s))", len(rows))
    except Exception as exc:
        logger.warning("quota sync bootstrap failed — %s", exc)
