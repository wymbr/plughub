"""
deployments_client.py
Cliente mínimo do agent-registry para a lente `deploy` do bench de Agentes
(Arc 6 Fase 2 — observabilidade por deploy).

Decisão D1 da spec (docs/product/arc6-phase2-deploy-observability-spec.md): o
deploy timeline de uma skill é lido em query-time direto do agent-registry —
GET {agent_registry_url}/v1/skills/{skill_id}/deployments — sem tabela
`analytics.deploy_events`, sem consumer Kafka. `skill_deployments` já existe e
serve.

Degradação graciosa: URL vazia, erro HTTP, timeout ou skill inexistente (404)
→ retorna [] e a série da lente sai sem `deploy_markers` (nunca 500). Cache TTL
curto em memória por (tenant, skill) para não bater no registry a cada render.

Forma de cada deployment retornado (normalizado p/ markers/epochs do P2-B):
    {
      "deploy_id":     str,      # id do registro skillDeployment
      "skill_id":      str,
      "version_label": str | None,  # version do skill no momento do deploy
      "deployed_at":   str | None,  # ISO 8601
      "deployed_by":   str | None,
    }
Ordenado por deployed_at desc (como o agent-registry devolve).
"""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger("plughub.analytics.deployments_client")

_TTL_S = 60.0
# (kind, tenant_id, entity_id) -> (expires_at, deployments)   kind ∈ {"skill","pool"}
_cache: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}


def _normalize(raw: dict) -> dict:
    """Reduz um registro de deployment do agent-registry aos campos da lente."""
    return {
        "deploy_id":     raw.get("id"),
        "skill_id":      raw.get("skill_id"),
        "version_label": raw.get("version"),
        "deployed_at":   raw.get("deployed_at"),
        "deployed_by":   raw.get("deployed_by"),
    }


async def _fetch_deployments(base_url: str, path: str, tenant_id: str,
                             cache_key: tuple[str, str, str], limit: int) -> list[dict]:
    """Núcleo compartilhado: GET {base}{path}/deployments, cache + degradação."""
    hit = _cache.get(cache_key)
    if hit and hit[0] > time.monotonic():
        return hit[1]

    deployments: list[dict] = []
    try:
        url = f"{base_url.rstrip('/')}{path}"
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                url,
                params={"limit": limit},
                headers={"x-tenant-id": tenant_id},
            )
            resp.raise_for_status()
            raw_list = resp.json().get("deployments") or []
            deployments = [_normalize(d) for d in raw_list if isinstance(d, dict)]
    except Exception as exc:
        logger.warning("deployments unavailable %s: %s", cache_key, exc)
        # cache negativo curto também — evita marteladas quando o registry está fora

    _cache[cache_key] = (time.monotonic() + _TTL_S, deployments)
    return deployments


async def fetch_skill_deployments(
    base_url: str, tenant_id: str, skill_id: str, *, limit: int = 200,
) -> list[dict]:
    """Histórico de deploys de uma skill (newest-first) ou [] em degradação."""
    if not base_url or not skill_id:
        return []
    return await _fetch_deployments(
        base_url, f"/v1/skills/{skill_id}/deployments",
        tenant_id, ("skill", tenant_id, skill_id), limit,
    )


async def fetch_pool_deployments(
    base_url: str, tenant_id: str, pool_id: str, *, limit: int = 200,
) -> list[dict]:
    """Deploys que atingiram um pool (Arc 6 Fase 2 — lente ancorada no pool).
    Newest-first; cada item normalizado carrega `skill_id`+`version_label`. [] em degradação."""
    if not base_url or not pool_id:
        return []
    return await _fetch_deployments(
        base_url, f"/v1/pools/{pool_id}/deployments",
        tenant_id, ("pool", tenant_id, pool_id), limit,
    )
