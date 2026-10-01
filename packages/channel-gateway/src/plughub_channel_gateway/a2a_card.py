"""
a2a_card.py — borda pública do AgentCard A2A (AAS-03; adr-a2a-server-binding D2).

    GET {base}/a2a/{slug}/.well-known/agent-card.json

O card é PROJEÇÃO montada no agent-registry (`GET /v1/a2a-cards/{slug}`); aqui fica só a
borda: a URL pública (`PLUGHUB_A2A_PUBLIC_BASE_URL`), um cache curto e a tradução das
recusas.

Três desfechos, e cada um com a sua cara pública:

  ok           → 200 com o card (cache 30 s, como o D2 pede: promover muda o card)
  refused      → 404 MUDO. O motivo (`not_discoverable`, `no_current_deploy`, …) vai ao
                 LOG, nunca ao chamador: dizer "existe, mas não é público" a um anônimo é
                 oráculo de enumeração.
  unavailable  → 503. Registry fora do ar não é "não existe" — 404 ali seria afirmar uma
                 ausência que ninguém mediu. NUNCA entra no cache.

Sem `a2a_public_base_url`, o card não tem como dizer ao cliente onde falar: 503 e ERROR
nomeando a env, em vez de inventar a URL pelo `Host` da requisição (que atrás de proxy é a
URL errada, e é o valor plausível que ninguém confere).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal, Optional

import httpx

logger = logging.getLogger("plughub.channel-gateway.a2a-card")

CardOutcome = Literal["ok", "refused", "unavailable"]

CACHE_TTL_S = 30.0

# Recusas que são ESTADO NORMAL (não é defeito haver endereço que não é público). As demais
# são configuração quebrada — endereço apontando para pool que não pode atender — e saem
# em WARNING, que é quem alguém lê.
_QUIET_REFUSALS = frozenset({"endpoint_not_found", "not_discoverable"})


@dataclass(frozen=True)
class CardResult:
    outcome: CardOutcome
    card:    Optional[dict] = None
    reason:  str = ""
    pool_id: Optional[str] = None


_cache: dict[tuple[str, str], tuple[CardResult, float]] = {}


def clear_cache() -> None:
    _cache.clear()


async def fetch_card(
    slug:          str,
    *,
    tenant_id:     str,
    registry_url:  str,
    base_url:      str,
    timeout_s:     float = 2.0,
    client:        Optional[httpx.AsyncClient] = None,
) -> CardResult:
    key = (tenant_id, slug)
    hit = _cache.get(key)
    if hit and time.monotonic() < hit[1]:
        return hit[0]

    url = f"{registry_url.rstrip('/')}/v1/a2a-cards/{slug}"
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout_s) as c:
                r = await c.get(url, params={"base_url": base_url}, headers={"x-tenant-id": tenant_id})
        else:
            r = await client.get(url, params={"base_url": base_url}, headers={"x-tenant-id": tenant_id})
    except httpx.HTTPError as exc:
        return CardResult("unavailable", reason=f"registry inalcançável: {type(exc).__name__}")

    if r.status_code == 200:
        res = CardResult("ok", card=r.json())
    elif r.status_code == 404:
        try:
            body = r.json()
        except ValueError:
            body = {}
        res = CardResult("refused", reason=str(body.get("reason") or "unknown"), pool_id=body.get("pool_id"))
    else:
        # 400 (base_url recusada) e 5xx são falha de quem monta, não ausência do endereço.
        return CardResult("unavailable", reason=f"registry respondeu {r.status_code}: {r.text[:200]}")

    _cache[key] = (res, time.monotonic() + CACHE_TTL_S)
    return res


def log_refusal(slug: str, res: CardResult) -> None:
    level = logging.INFO if res.reason in _QUIET_REFUSALS else logging.WARNING
    logger.log(level, "a2a card recusado slug=%s pool=%s motivo=%s", slug, res.pool_id, res.reason)
