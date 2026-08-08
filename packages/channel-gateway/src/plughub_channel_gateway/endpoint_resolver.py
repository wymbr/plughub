"""
endpoint_resolver.py
Resolves channel identifiers to pool_ids by querying the agent-registry
channel-endpoints API with a short in-process TTL cache.

Layer 2 of the channel-gateway routing stack:

  URL path slug / phone number / DID
      ↓  (this module)
  GET /v1/channel-endpoints?channel={ch}&identifier={id}&active=true
      ↓
  pool_id  (or None → caller applies backward-compat fallback)

The cache prevents repeated HTTP calls on the hot WebSocket connect path
while still reflecting configuration changes within `endpoint_cache_ttl_s`
seconds (default 30 s).

Cache invalidation note
-----------------------
The `invalidate()` helper is exposed so that a future registry.changed Kafka
consumer can drop stale entries immediately.  Until that consumer is wired,
the TTL-based expiry is the only invalidation mechanism.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Literal, Optional

import httpx

logger = logging.getLogger("plughub.channel-gateway.endpoint-resolver")

# ─── Desfecho da resolução ───────────────────────────────────────────────────
#
# `Optional[str]` colapsava DUAS coisas diferentes em `None`: *"perguntei e não
# existe"* e *"não consegui perguntar"*. Enquanto todo caminho tinha fallback
# permissivo, a diferença não aparecia — quem chama tratava os dois como "usa o
# default". A Fase C do ADR de webhook torna a distinção necessária por dois
# motivos independentes:
#
#   1. **O log da fase é uma MEDIDA** ("quem ainda depende do fallback"). Uma
#      indisponibilidade do agent-registry contada como "chamador não migrado"
#      contamina exatamente o número que decide a Fase E.
#   2. **Na Fase E o fallback sai.** A partir dali, `not_found` deve virar 404 e
#      `unavailable` NÃO deve — 404 afirma que o endereço não existe, o que é
#      mentira sobre uma falha de rede. Sem separar agora, a Fase E herdaria a
#      confusão num ponto em que ela já derruba tráfego.
#
# `unavailable` NUNCA é cacheado: cachear falha transforma um soluço de 2 s em
# 30 s de resolução degradada, e o cache existe para poupar rede, não para
# lembrar de erros.
# `origin_refused` — a linha EXISTE, mas a procedência dela não é aceita nesta porta
# (ADR §7.6.3: a porta externa serve só `origin='external'`). Distinto de `not_found`
# porque a ação corretiva é oposta: ali falta semear, aqui o endereço é de outra porta.
ResolveOutcome = Literal["found", "not_found", "unavailable", "origin_refused"]

# ─── In-process cache ────────────────────────────────────────────────────────
# Key:   (tenant_id, channel, identifier)
# Value: (resolved_pool_id | None, origin | None, outcome, expires_at_monotonic)
#
# Guardar None com `not_found` significa "olhei e não achei" — evita martelar o
# registry para identificadores desconhecidos a cada tentativa de conexão.
#
# ⚠️ O CACHE GUARDA A RESOLUÇÃO CRUA, NUNCA O VEREDICTO FILTRADO. A chave é
# (tenant, canal, identificador) e as DUAS portas de webhook a compartilham, com
# filtros de procedência diferentes. Aplicar o filtro antes de cachear faria a
# primeira porta a consultar decidir pela outra: um `origin_refused` gravado pela
# porta externa viraria "não existe" para a interna, e o endereço sumiria por até
# `cache_ttl_s` — intermitência dependente de quem chamou primeiro, que é o tipo de
# defeito que não se reproduz sob investigação. O filtro é aplicado NA SAÍDA.
@dataclass(frozen=True)
class ResolvedEndpoint:
    """
    O que o registro sabe sobre um endereço. Virou objeto quando a autenticação
    entrou: a tupla `(pool_id, origin, outcome)` já era o limite do legível, e cada
    campo novo empurrava todos os call sites a desempacotar posições que não usam.

    `token_hash` só vem preenchido quando o resolver apresenta credencial de serviço
    — sem ela o agent-registry omite o campo (é material de credencial, e a leitura
    geral é a mesma que a UI consome).
    """
    pool_id:       Optional[str]
    origin:        Optional[str]
    auth_required: bool
    token_hash:    Optional[str]
    outcome:       ResolveOutcome


_CacheKey   = tuple[str, str, str]
_CacheValue = tuple[ResolvedEndpoint, float]

_cache: dict[_CacheKey, _CacheValue] = {}
_lock  = asyncio.Lock()


# ─── Public API ──────────────────────────────────────────────────────────────

async def resolve_pool(
    *,
    channel:            str,
    identifier:         str,
    tenant_id:          str,
    agent_registry_url: str,
    cache_ttl_s:        int   = 30,
    http_timeout_s:     float = 2.0,
) -> Optional[str]:
    """
    Return the pool_id mapped to (channel, identifier) for the given tenant,
    or None if no active record exists or the registry cannot be reached.

    The caller is responsible for applying a fallback when None is returned
    (typically: treat the identifier itself as the pool_id for backward compat).

    Quem precisa saber POR QUE deu None (log honesto, ou decidir entre 404 e 503)
    deve usar `resolve_pool_ex`. Esta função permanece como está para os chamadores
    de fallback permissivo (webchat), onde o motivo não muda a decisão.

    Parameters
    ----------
    channel:            Channel type, e.g. "webchat", "whatsapp".
    identifier:         The external identifier — webchat slug, WhatsApp DID, etc.
    tenant_id:          Tenant to scope the lookup.
    agent_registry_url: Base URL of the agent-registry service.
    cache_ttl_s:        How long (seconds) to cache a positive or negative result.
    http_timeout_s:     HTTP connect+read timeout; kept low to avoid blocking WS.
    """
    pool_id, _outcome = await resolve_pool_ex(
        channel            = channel,
        identifier         = identifier,
        tenant_id          = tenant_id,
        agent_registry_url = agent_registry_url,
        cache_ttl_s        = cache_ttl_s,
        http_timeout_s     = http_timeout_s,
    )
    return pool_id


async def resolve_pool_ex(
    *,
    channel:            str,
    identifier:         str,
    tenant_id:          str,
    agent_registry_url: str,
    cache_ttl_s:        int   = 30,
    http_timeout_s:     float = 2.0,
    allowed_origins:    Optional[frozenset[str]] = None,
    service_token:      str   = "",
) -> tuple[Optional[str], ResolveOutcome]:
    """
    Wrapper de compatibilidade sobre `resolve_endpoint` para quem só precisa do par
    `(pool_id, motivo)`. Ver `resolve_endpoint` para a descrição dos desfechos.
    """
    ep = await resolve_endpoint(
        channel            = channel,
        identifier         = identifier,
        tenant_id          = tenant_id,
        agent_registry_url = agent_registry_url,
        cache_ttl_s        = cache_ttl_s,
        http_timeout_s     = http_timeout_s,
        allowed_origins    = allowed_origins,
        service_token      = service_token,
    )
    return ep.pool_id, ep.outcome


async def resolve_endpoint(
    *,
    channel:            str,
    identifier:         str,
    tenant_id:          str,
    agent_registry_url: str,
    cache_ttl_s:        int   = 30,
    http_timeout_s:     float = 2.0,
    allowed_origins:    Optional[frozenset[str]] = None,
    service_token:      str   = "",
) -> ResolvedEndpoint:
    """
    Resolve um endereço e devolve tudo que o registro sabe sobre ele, incluindo
    **por que** o resultado é o que é:

      found          — existe registro ativo e a procedência é aceita
      not_found      — perguntei ao registry e não há linha
      unavailable    — não consegui perguntar (rede, HTTP != 2xx)
      origin_refused — a linha EXISTE, mas a procedência não é aceita nesta porta

    `allowed_origins=None` (default) aceita qualquer procedência.
    `service_token` habilita o retorno de `token_hash` (material de credencial).
    """
    cache_key: _CacheKey = (tenant_id, channel, identifier)
    now = time.monotonic()

    # ── Fast path: cache hit (lock-free read) ────────────────────────────────
    entry = _cache.get(cache_key)
    if entry is not None:
        resolved, expires_at = entry
        if now < expires_at:
            return _apply_origin_filter(resolved, allowed_origins)

    # ── Slow path: refresh under lock (prevents stampede) ───────────────────
    async with _lock:
        # Double-check: another coroutine may have already refreshed while we
        # were waiting for the lock.
        entry = _cache.get(cache_key)
        if entry is not None:
            resolved, expires_at = entry
            if now < expires_at:
                return _apply_origin_filter(resolved, allowed_origins)

        resolved = await _fetch_pool(
            channel            = channel,
            identifier         = identifier,
            tenant_id          = tenant_id,
            agent_registry_url = agent_registry_url,
            http_timeout_s     = http_timeout_s,
            service_token      = service_token,
        )

        # Falha de transporte NÃO entra no cache — ver `ResolveOutcome`.
        # O que entra é a resolução CRUA (sem filtro) — ver o bloco do cache.
        if resolved.outcome != "unavailable":
            _cache[cache_key] = (resolved, time.monotonic() + cache_ttl_s)
        return _apply_origin_filter(resolved, allowed_origins)


def _apply_origin_filter(
    resolved:        ResolvedEndpoint,
    allowed_origins: Optional[frozenset[str]],
) -> ResolvedEndpoint:
    """
    Aplica o filtro de procedência NA SAÍDA (nunca antes do cache — ver o bloco do
    cache para o defeito que isso evita).

    Linha sem `origin` (resposta anterior à coluna) conta como `external`: é o único
    caso que existia antes do campo, e assumi-lo preserva o comportamento de quem já
    estava cadastrado. Inventar procedência para dado antigo recusaria endpoint
    legítimo durante uma janela de deploy.
    """
    if resolved.pool_id is None or allowed_origins is None:
        return resolved
    if (resolved.origin or "external") in allowed_origins:
        return resolved
    # Recusado: zera o pool (não há para onde rotear) E o hash — nenhum consumidor
    # de uma resolução recusada tem o que fazer com material de credencial.
    return ResolvedEndpoint(
        pool_id=None, origin=resolved.origin, auth_required=resolved.auth_required,
        token_hash=None, outcome="origin_refused",
    )


def invalidate_all() -> None:
    """
    Esvazia o cache inteiro. Usado quando chega um `registry.changed` **sem
    tenant_id** — evento malformado ou de um produtor que não o preenche.

    Derrubar tudo é o lado seguro do trade-off: o custo é uma consulta HTTP por
    endereço no próximo disparo (raro, e o registry é local); o custo do outro lado é
    servir configuração revogada até o TTL. Para um cache, correção vale mais que
    economia — a economia é o motivo de ele existir, não a razão de ele existir.
    """
    n = len(_cache)
    _cache.clear()
    if n:
        logger.info("endpoint-resolver: cache INTEIRO invalidado (%d entradas)", n)


def invalidate(*, tenant_id: str, channel: Optional[str] = None) -> None:
    """
    Evict cache entries for a tenant, optionally scoped to a channel.

    Call this when a registry.changed event is received so that the next
    connection attempt immediately picks up the new mapping.
    """
    to_drop = [
        k for k in _cache
        if k[0] == tenant_id and (channel is None or k[1] == channel)
    ]
    for k in to_drop:
        _cache.pop(k, None)

    if to_drop:
        logger.debug(
            "endpoint-resolver: invalidated %d cache entries (tenant=%s channel=%s)",
            len(to_drop), tenant_id, channel or "*",
        )


# ─── Internal helpers ─────────────────────────────────────────────────────────

async def _fetch_pool(
    *,
    channel:            str,
    identifier:         str,
    tenant_id:          str,
    agent_registry_url: str,
    http_timeout_s:     float,
    service_token:      str = "",
) -> ResolvedEndpoint:
    """
    Perform a single HTTP GET to agent-registry and return the first matching active
    endpoint. `unavailable` é reservado a "não consegui perguntar"; ausência de linha
    é `not_found`. **Não aplica filtro de procedência** — devolve o `origin` cru para
    quem chama decidir (e para o cache guardar).

    `service_token` é o que faz o agent-registry incluir `token_hash` na resposta.
    """
    url = f"{agent_registry_url.rstrip('/')}/v1/channel-endpoints"
    params = {
        "channel":    channel,
        "identifier": identifier,
        "active":     "true",
    }
    headers = {"X-Tenant-Id": tenant_id}
    if service_token:
        headers["x-service-token"] = service_token

    try:
        async with httpx.AsyncClient(timeout=http_timeout_s) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "endpoint-resolver: registry returned HTTP %s "
            "(channel=%s identifier=%s tenant=%s)",
            exc.response.status_code, channel, identifier, tenant_id,
        )
        return _unavailable()
    except Exception as exc:
        logger.warning(
            "endpoint-resolver: could not reach agent-registry "
            "(channel=%s identifier=%s tenant=%s): %s",
            channel, identifier, tenant_id, exc,
        )
        return _unavailable()

    endpoints: list[dict] = data.get("endpoints", [])
    if not endpoints:
        logger.debug(
            "endpoint-resolver: no active endpoint (channel=%s identifier=%s tenant=%s)",
            channel, identifier, tenant_id,
        )
        return ResolvedEndpoint(None, None, False, None, "not_found")

    row      = endpoints[0]
    pool_id  = str(row["pool_id"])
    origin   = str(row.get("origin") or "external")
    auth_req = bool(row.get("auth_required") or False)
    # `token_hash` ausente ⇒ ou o endpoint não tem token, ou NÃO APRESENTAMOS
    # credencial de serviço. Os dois casos chegam aqui como None; quem distingue é
    # o chamador, comparando com `auth_required` (ver o fail-closed em main.py).
    tok_hash = row.get("token_hash") or None
    logger.info(
        "endpoint-resolver: %s/%s → pool=%s (origin=%s, auth=%s, tenant=%s)",
        channel, identifier, pool_id, origin, auth_req, tenant_id,
    )
    return ResolvedEndpoint(pool_id, origin, auth_req, tok_hash, "found")


def _unavailable() -> ResolvedEndpoint:
    return ResolvedEndpoint(None, None, False, None, "unavailable")
