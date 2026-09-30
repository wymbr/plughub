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


# ── WHK-02 (2026-09-30): o endereço das portas PÚBLICAS de contato ─────────────
#
# `/ws/chat/{x}` e `/ws/webrtc/{x}` resolviam `x` como `ChannelEndpoint` e, sem registro,
# usavam o próprio `x` como pool ("backward compatibility") — inclusive quando o registry
# estava FORA do ar. É a família da WHK-01 na porta publicada na borda: um `x` inventado
# virava contato numa fila que ninguém atende. Decisão do dono: `x` desconhecido só vira
# pool se o pool EXISTE (a página de pesquisa por link, 9 gates e 2 cenários e2e abrem o
# WS pelo pool cru, então "só ChannelEndpoint" quebraria produto).

AddressVerdict = Literal["endpoint", "pool", "not_found", "unavailable"]


class ContactAddress:
    """O que a porta pública sabe sobre o endereço `x`, sem palpite."""
    __slots__ = ("verdict", "pool_id", "reason", "endpoint")

    def __init__(self, verdict: AddressVerdict, pool_id: str = "", reason: str = "",
                 endpoint: object | None = None) -> None:
        self.verdict = verdict
        self.pool_id = pool_id
        self.reason = reason
        self.endpoint = endpoint

    @property
    def ok(self) -> bool:
        return self.verdict in ("endpoint", "pool")


async def resolve_contact_address(
    *,
    channel:            str,
    identifier:         str,
    tenant_id:          str,
    agent_registry_url: str,
    service_token:      str = "",
    cache_ttl_s:        int = 30,
) -> ContactAddress:
    """`endpoint` (cadastrado) · `pool` (pool que EXISTE) · `not_found` · `unavailable`.

    `unavailable` = não deu para perguntar — quem chama RECUSA, nunca segue com `x`.
    """
    from .endpoint_resolver import resolve_endpoint

    if not identifier:
        return ContactAddress("not_found", reason="endereço vazio")
    if not agent_registry_url:
        return ContactAddress("unavailable", reason="agent_registry_url não configurada")
    ep = await resolve_endpoint(
        channel            = channel,
        identifier         = identifier,
        tenant_id          = tenant_id,
        agent_registry_url = agent_registry_url,
        cache_ttl_s        = cache_ttl_s,
    )
    if ep.pool_id:
        return ContactAddress("endpoint", pool_id=ep.pool_id, endpoint=ep)
    if ep.outcome == "unavailable":
        return ContactAddress("unavailable", reason=f"registro de canais inalcançável ({channel}/{identifier})")
    veredito, motivo = await pool_existence(
        tenant_id          = tenant_id,
        pool_id            = identifier,
        agent_registry_url = agent_registry_url,
        service_token      = service_token,
    )
    if veredito == "exists":
        return ContactAddress("pool", pool_id=identifier)
    return ContactAddress(veredito, reason=motivo)
