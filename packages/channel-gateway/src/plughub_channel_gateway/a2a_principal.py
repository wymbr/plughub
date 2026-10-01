"""
a2a_principal.py — quem está chamando pelo canal `a2a` (AAS-04; adr-a2a-server-binding D6, D7).

A credencial é a do `agent_principal` externo, emitida pelo admin do tenant no auth-api, e chega
como `Authorization: Bearer <credencial>`. O gateway NÃO guarda credencial: pergunta ao auth-api
(`POST /auth/v1/agent-principals/introspect`, só serviço, formato RFC 7662) e guarda o veredicto
por 30 s — o mesmo prazo do card. Desativar ou rotacionar vale na borda em até 30 s.

Quatro desfechos, cada um com a sua cara:

  ok           → o principal, com o TENANT DELE (D7: no canal a2a o tenant vem exclusivamente
                 da credencial — nunca de corpo, query ou header)
  missing      → sem `Authorization: Bearer`                    → 401
  invalid      → o auth-api disse `active: false`               → 401 (o motivo fica no log dele)
  unavailable  → não deu para perguntar                         → 503, e NUNCA entra no cache:
                 "não consegui conferir" não é "credencial inválida", e cachear transformaria um
                 soluço de rede em 30 s de recusa.

A chave do cache é o SHA-256 da credencial, nunca o texto.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Literal, Optional

import httpx

logger = logging.getLogger("plughub.channel-gateway.a2a-principal")

AuthOutcome = Literal["ok", "missing", "invalid", "unavailable"]
CACHE_TTL_S = 30.0


@dataclass(frozen=True)
class Principal:
    sub:           str
    tenant_id:     str
    kind:          str
    display_name:  str
    allowed_pools: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class AuthResult:
    outcome:   AuthOutcome
    principal: Optional[Principal] = None
    reason:    str = ""


_cache: dict[str, tuple[AuthResult, float]] = {}


def clear_cache() -> None:
    _cache.clear()


def bearer(authorization: str) -> str:
    scheme, _, cred = (authorization or "").partition(" ")
    return cred.strip() if scheme.lower() == "bearer" else ""


async def authenticate(
    authorization: str,
    *,
    auth_api_url:  str,
    service_token: str,
    timeout_s:     float = 2.0,
    client:        Optional[httpx.AsyncClient] = None,
) -> AuthResult:
    cred = bearer(authorization)
    if not cred:
        return AuthResult("missing")
    if not auth_api_url or not service_token:
        logger.error("a2a: PLUGHUB_AUTH_API_URL/PLUGHUB_AUTH_API_SERVICE_TOKEN vazio — nenhuma credencial "
                     "A2A é conferida, e toda chamada recebe 503")
        return AuthResult("unavailable", reason="introspecção não configurada no gateway")

    key = hashlib.sha256(cred.encode("utf-8")).hexdigest()
    hit = _cache.get(key)
    if hit and time.monotonic() < hit[1]:
        return hit[0]

    url = f"{auth_api_url.rstrip('/')}/auth/v1/agent-principals/introspect"
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout_s) as c:
                r = await c.post(url, json={"credential": cred}, headers={"x-service-token": service_token})
        else:
            r = await client.post(url, json={"credential": cred}, headers={"x-service-token": service_token})
    except httpx.HTTPError as exc:
        return AuthResult("unavailable", reason=f"auth-api inalcançável: {type(exc).__name__}")
    if r.status_code != 200:
        return AuthResult("unavailable", reason=f"auth-api respondeu {r.status_code}: {r.text[:200]}")

    body = r.json()
    if body.get("active") is True and body.get("tenant_id") and body.get("sub"):
        res = AuthResult("ok", principal=Principal(
            sub           = str(body["sub"]),
            tenant_id     = str(body["tenant_id"]),
            kind          = str(body.get("kind") or ""),
            display_name  = str(body.get("display_name") or ""),
            allowed_pools = frozenset(body.get("allowed_pools") or []),
        ))
    else:
        res = AuthResult("invalid")
    _cache[key] = (res, time.monotonic() + CACHE_TTL_S)
    return res
