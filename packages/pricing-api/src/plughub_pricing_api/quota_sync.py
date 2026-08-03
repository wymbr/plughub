"""
quota_sync.py
Capacity-governance item 1 — pricing escreve a quota de admissão no Redis.

A cada mutação de resources (upsert / delete / activate / deactivate de reserva)
recalcula C do tenant — capacidade contratada de agentes (ai_agent + human_agent,
base + reservas comerciais ativas, todas as instalações) — e grava:

    {tenant_id}:quota:max_concurrent_sessions = C

⚠️ **LEITORES — corrigido em 2026-08-03.** Este docstring descrevia a admissão híbrida
(`shared = C − Σ session_reservation`, rejeição `shared_full`), modelo **removido em
2026-08-02** (fatia 3 do arco de capacidade). Ele somava licença humana com licença de IA
num pote único — a mesma falácia de aditividade que o rollup recusa no topo —, e por isso
recusava contato real com humano ocioso. Quem lesse isto hoje procuraria um gate que não
existe, e pior: concluiria que `max_concurrent_sessions` governa admissão.

Estado atual de cada chave que este módulo escreve:

  {t}:quota:capacity:ai_agent        → **é o teto de admissão.** Único gate de sessão
      que sobrou (`AdmissionController`: `kind:ai ≤ C_ai`). Sessão em pool
      `agent_kind='ai'`; rejeição só na porta, com `cause="quota"`.
  {t}:quota:capacity:human_agent     → **gate de LOGIN**, não de sessão. Licença humana é
      por login (`agent_login` → `human_capacity_exhausted`), e gateá-la de novo por
      sessão seria gate duplo na unidade errada.
  {t}:quota:max_concurrent_sessions  → **não governa admissão.** Sobrevive como número de
      PROVISIONAMENTO, cobrado por `capacity.ts/deployViolation` (Σ declarada nos deploys
      ≤ C). Mistura moedas também — mas isso é o defeito C, de outra fatia; trocá-lo aqui
      seria construir a fatia 4 no meio desta.

  - mcp-server `checkConcurrentSessions` (segue lendo `max_concurrent_sessions`)

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
# Item 2 (2026-06-05) — quotas por tipo, base dos gates de criação:
#   capacity:ai_agent    → gate de sessões em pools agent_kind='ai' (admissão)
#   capacity:human_agent → gate de logins humanos concorrentes (registerHumanAgent)
_QUOTA_TYPE_KEY = "{tenant_id}:quota:capacity:{resource_type}"
_AGENT_TYPES    = ("ai_agent", "human_agent")


async def sync_tenant(
    redis_client: "aioredis.Redis | None",
    pg_pool:      asyncpg.Pool,
    tenant_id:    str,
) -> int | None:
    """Recalcula C do tenant e grava/remove as quotas (total + por tipo)."""
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

        # Por tipo (item 2): C_ai / C_human — mesmo recompute, mesma semântica DEL.
        by_type = {t["resource_type"]: int(t["total"] or 0) for t in cap.get("by_type", [])}
        for rtype in _AGENT_TYPES:
            tkey = _QUOTA_TYPE_KEY.format(tenant_id=tenant_id, resource_type=rtype)
            tval = by_type.get(rtype, 0)
            if tval > 0:
                await redis_client.set(tkey, str(tval))
                logger.info("quota sync: %s = %d", tkey, tval)
            else:
                await redis_client.delete(tkey)
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
