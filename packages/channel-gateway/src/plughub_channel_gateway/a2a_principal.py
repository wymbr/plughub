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
    # AAS-09 — só no `customer_agent` (o token do PRÓPRIO cliente); None no `partner`
    customer_id:       Optional[str] = None
    proof_mechanism:   Optional[str] = None
    proof_verified_at: Optional[str] = None
    mandate:           tuple[str, ...] = ()
    exp:               Optional[int] = None    # epoch s — validade curta (D6)
    max_active_tasks:  Optional[int] = None    # cota por principal (D9); None = sem cota
    max_tasks_per_day: Optional[int] = None


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
        # AAS-09 — o cache de 30 s não estica a validade: token vencido no meio da janela
        # deixa de valer na hora, não no próximo pergunta-ao-auth-api.
        p = hit[0].principal
        if p is not None and p.exp is not None and time.time() >= p.exp:
            _cache.pop(key, None)
            logger.info("a2a: credencial do principal %s venceu (cache) — recusada", p.sub)
            return AuthResult("invalid")
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
    kind = str(body.get("kind") or "")
    if body.get("active") is True and body.get("tenant_id") and body.get("sub"):
        if kind == "customer_agent" and not (body.get("customer_id") and body.get("exp")):
            # O titular vem DO token (D6): sem titular ou sem validade, não há em nome de quem
            # falar — recusa, nunca segue como `partner`.
            logger.error("a2a: customer_agent %s sem titular ou validade na introspecção — recusado",
                         body.get("sub"))
            res = AuthResult("invalid")
        else:
            def _int(v: object) -> Optional[int]:
                return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
            res = AuthResult("ok", principal=Principal(
                sub               = str(body["sub"]),
                tenant_id         = str(body["tenant_id"]),
                kind              = kind,
                display_name      = str(body.get("display_name") or ""),
                allowed_pools     = frozenset(body.get("allowed_pools") or []),
                customer_id       = str(body["customer_id"]) if body.get("customer_id") else None,
                proof_mechanism   = body.get("proof_mechanism") or None,
                proof_verified_at = body.get("proof_verified_at") or None,
                mandate           = tuple(str(m) for m in (body.get("mandate") or [])),
                exp               = _int(body.get("exp")),
                max_active_tasks  = _int(body.get("max_active_tasks")),
                max_tasks_per_day = _int(body.get("max_tasks_per_day")),
            ))
    else:
        res = AuthResult("invalid")
    _cache[key] = (res, time.monotonic() + CACHE_TTL_S)
    return res
