"""
pools_client.py
Cliente mínimo do agent-registry para descobrir quais pools são INTERNOS (E2f).

CONTEXTO. Desde o arco de detach, o wrap-up destacado nasce como uma SESSÃO própria
(canal `webhook`, `customer_id` herdado do contato de origem) e não carrega nenhum
campo que a distinga de um workflow de negócio legítimo. Sem isso, ela entra nas
métricas como se fosse um contato: volume quase dobrado nos pools humanos, TMA
diluído (para sessão webhook o handle time é wall-clock, e o item fica parado na
inbox até o claim) e um contato fantasma no Histórico do Cliente 360.

O discriminador é o POOL — `pools.purpose` ∈ {contact, internal} — porque a sessão
de wrap-up é roteada a um pool diferente do pool humano, e tanto `sessions` quanto
`segments` já carregam `pool_id`. Ver a migration `20260729010000_pool_purpose`.

É SEGREGAÇÃO, NÃO SUPRESSÃO: o pool interno mantém as métricas dele (o TMA de um
pool de wrap-up É o tempo de ACW). O que sai são os TOTAIS de atendimento.

─────────────────────────────────────────────────────────────────────────────
DEGRADAÇÃO — por que este cliente NÃO copia o `deployments_client`
─────────────────────────────────────────────────────────────────────────────
O `deployments_client` degrada para `[]` e a lente perde um overlay: falha visível
e inofensiva. Aqui a assimetria é outra — degradar para "conjunto vazio" significa
**voltar a contar wrap-up como contato**, ou seja, devolver um número errado com
cara de certo. É exatamente o que o CLAUDE.md § Postura de Engenharia proíbe.

Por isso:
  • sucesso        → cacheia por _TTL_S e guarda como "último bom" (sem expiração);
  • falha COM último bom → reusa o último bom + WARNING nomeando a consequência;
  • falha SEM último bom → ERROR explícito (o número VAI sair inflado) + retry curto.

Nunca há degradação muda.
"""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger("plughub.analytics.pools_client")

_TTL_S = 60.0
# Retry curto quando degradado — não martela o registry, mas recupera rápido.
_DEGRADED_TTL_S = 15.0

# tenant_id -> (expires_at, internal pool ids)
_cache: dict[str, tuple[float, frozenset[str]]] = {}
# tenant_id -> último valor obtido com sucesso (sem expiração; a rede da degradação)
_last_good: dict[str, frozenset[str]] = {}

INTERNAL = "internal"


def _reset_cache_for_tests() -> None:
    """Limpa o estado do módulo. Só para testes."""
    _cache.clear()
    _last_good.clear()


async def fetch_internal_pools(base_url: str, tenant_id: str) -> frozenset[str]:
    """Pools com `purpose == "internal"` do tenant.

    Retorna um frozenset de `pool_id`. Degradação sempre barulhenta (ver docstring
    do módulo). `base_url` vazia = integração não configurada → conjunto vazio com
    aviso único por janela de cache.
    """
    if not tenant_id:
        return frozenset()

    hit = _cache.get(tenant_id)
    if hit and hit[0] > time.monotonic():
        return hit[1]

    if not base_url:
        logger.warning(
            "internal-pool scope DISABLED tenant=%s: agent_registry_url não configurada — "
            "sessões de pools internos (wrap-up) seguem contando como contato",
            tenant_id,
        )
        _cache[tenant_id] = (time.monotonic() + _DEGRADED_TTL_S, frozenset())
        return frozenset()

    try:
        url = f"{base_url.rstrip('/')}/v1/pools"
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url, headers={"x-tenant-id": tenant_id})
            resp.raise_for_status()
            pools = (resp.json() or {}).get("pools") or []
    except Exception as exc:
        previous = _last_good.get(tenant_id)
        if previous is not None:
            logger.warning(
                "agent-registry indisponível tenant=%s (%s) — reusando último conjunto "
                "conhecido de pools internos (%d): %s",
                tenant_id, exc, len(previous), sorted(previous),
            )
            _cache[tenant_id] = (time.monotonic() + _DEGRADED_TTL_S, previous)
            return previous
        logger.error(
            "agent-registry indisponível tenant=%s (%s) e SEM conjunto conhecido — "
            "contagens de contato e TMA sairão INFLADAS (sessões internas contadas "
            "como contato) até o registry responder",
            tenant_id, exc,
        )
        _cache[tenant_id] = (time.monotonic() + _DEGRADED_TTL_S, frozenset())
        return frozenset()

    internal = frozenset(
        str(p.get("pool_id"))
        for p in pools
        if isinstance(p, dict) and p.get("purpose") == INTERNAL and p.get("pool_id")
    )
    _cache[tenant_id] = (time.monotonic() + _TTL_S, internal)
    _last_good[tenant_id] = internal
    return internal
