"""
pool_existence.py — o pool endereçado existe? (WHK-01, 2026-09-29)

O gatilho por pool (`POST /v1/channels/webhook/pool/{pool_id}`) criava sessão para QUALQUER
`pool_id`. Medido: com um pool inexistente, 201 e uma sessão que o routing enfileirava em
`{t}:pool:{p}:queue` — fila de um pool que ninguém atende — até o teto de espera a encerrar
como `max_wait_exceeded`, motivo plausível que esconde "o pool não existe". A rota é anônima
por construção (CLAUDE.md, regra da borda do channel-gateway), então qualquer um na rede
interna enchia filas fantasmas.

Por que a pergunta é feita AQUI, na porta, e não no routing: o routing só conhece o pool pelo
cache `{t}:pool_config:{p}`, que expira — ali "não achei a config" não é "o pool não existe".
Quem sabe é o agent-registry, e a porta pode perguntar a ele.

Três respostas, e nenhuma é palpite:
  `exists`      — 200 do registry;
  `not_found`   — 404 do registry: o pool não existe NESTE tenant;
  `unavailable` — não consegui perguntar (sem URL, rede, 5xx, outro status). Quem chama
                  RECUSA com 503, nunca segue como se existisse.
"""
from __future__ import annotations

import logging
from typing import Literal

import httpx

logger = logging.getLogger("plughub.channel-gateway.pool-existence")

PoolExistence = Literal["exists", "not_found", "unavailable"]


async def pool_existence(
    *,
    tenant_id:          str,
    pool_id:            str,
    agent_registry_url: str,
    service_token:      str = "",
    timeout_s:          float = 5.0,
    transport:          httpx.AsyncBaseTransport | None = None,
) -> tuple[PoolExistence, str]:
    """Devolve (veredito, motivo). O motivo é para o log e para o corpo da recusa."""
    if not agent_registry_url:
        return "unavailable", "agent_registry_url não configurada"
    headers = {"x-tenant-id": tenant_id}
    if service_token:
        headers["x-service-token"] = service_token
    url = f"{agent_registry_url.rstrip('/')}/v1/pools/{pool_id}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
            r = await client.get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001 — o motivo vai adiante, nunca é engolido
        return "unavailable", f"registry inalcançável: {exc}"
    if r.status_code == 200:
        return "exists", ""
    if r.status_code == 404:
        return "not_found", f"pool '{pool_id}' não existe no tenant '{tenant_id}'"
    return "unavailable", f"registry respondeu HTTP {r.status_code}"
