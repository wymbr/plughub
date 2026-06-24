"""
coverage_client.py
Cliente mínimo da evaluation-api para o overlay do epoch da lente `deploy`
(Arc 6 Fase 2 — micro-fatia 1b, Opção II).

A curva FINALIZADA do epoch vem do ClickHouse (exata, via segment_id). Este
cliente busca o que o ClickHouse não tem por versão: a nota PROVISÓRIA (só
avaliações já pontuadas) e o `pending_n` (instâncias amostradas ainda não
finalizadas), por `(pool, deploy_version)`, lido em query-time da evaluation-api
(`GET /v1/evaluation/reports/deploy-coverage`).

Degradação graciosa: URL vazia, erro HTTP, timeout → retorna [] e o epoch sai
sem overlay (nunca 500). Cache TTL curto por (tenant, pool, janela).
"""
from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger("plughub.analytics.coverage_client")

_TTL_S = 60.0
# (tenant_id, pool_id, since, until) -> (expires_at, coverage_rows)
_cache: dict[tuple[str, str, str, str], tuple[float, list[dict]]] = {}


async def fetch_deploy_coverage(
    base_url: str, tenant_id: str, pool_id: str, since: str, until: str,
) -> list[dict]:
    """Cobertura por versão de um pool (provisional_avg/_n + pending_n) ou [] em
    degradação. Cada item: {pool_id, deploy_version, pending_n, provisional_n,
    provisional_avg}."""
    if not base_url or not pool_id:
        return []
    key = (tenant_id, pool_id, since, until)
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]

    coverage: list[dict] = []
    try:
        url = f"{base_url.rstrip('/')}/v1/evaluation/reports/deploy-coverage"
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url, params={
                "tenant_id": tenant_id,
                "pool_id":   pool_id,
                "from_dt":   since,
                "to_dt":     until,
            })
            resp.raise_for_status()
            coverage = resp.json().get("coverage") or []
    except Exception as exc:
        logger.warning("deploy-coverage unavailable %s: %s", key, exc)
        # cache negativo curto também — evita marteladas quando a API está fora

    _cache[key] = (time.monotonic() + _TTL_S, coverage)
    return coverage
