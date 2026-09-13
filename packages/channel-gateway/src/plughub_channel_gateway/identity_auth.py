"""
identity_auth.py — o portão das rotas de identidade do channel-gateway (IDN-06, 2026-09-13).

Medido antes de fechar: as rotas `/v1/channels/webhook/identity/*` e `/pending/*` não
pediam credencial nenhuma, o `tenant_id` vinha do CORPO, e a UI as proxia — **pelo
host da UI e sem login**, a busca devolvia o cadastro de clientes e o resolve
respondia. `/v1` é interno na allowlist da borda, mas a separação é de código, e
quem publicava era a própria UI.

Duas portas, uma casa:

  - **serviço** (`X-Service-Token` = `PLUGHUB_CHANNEL_GATEWAY_SERVICE_TOKEN`): os
    chamadores internos (mcp-server, mailing-api), que não têm usuário. O principal
    é IRRESTRITO e por isso é uma IDENTIDADE — `service:<X-Service-Name>` vai ao log.
    O tenant é o do corpo. ⚠️ Token vazio no gateway NÃO libera: a porta de serviço
    fica fechada e o boot avisa. Token errado é 401 — nunca cai calado na porta de
    usuário.
  - **usuário** (Bearer do auth-api, `plughub_authz`): só nas rotas que DECLARAM um
    campo ABAC. O tenant é o do JWT; um `tenant_id` divergente no pedido é 403.

Rota que não declara campo é INTERNA: usuário nenhum passa, com ou sem grant.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request
from plughub_authz import abac_can, bearer_from_header, verify_user_jwt

logger = logging.getLogger("plughub.channel-gateway.identity_auth")


@dataclass
class IdentityPrincipal:
    kind:      str                     # service | user
    sub:       str
    tenant_id: str | None              # None = serviço (o tenant vem do pedido)
    claims:    dict[str, Any] = field(default_factory=dict)


def identity_principal(
    request: Request,
    *,
    service_token: str,
    jwt_secret: str,
    user_grants: tuple[tuple[str, str, str], ...] = (),
) -> IdentityPrincipal:
    """Quem chama, ou levanta 401/403. `user_grants` = [(módulo, campo, acesso)], BASTA UM."""
    rota = request.url.path
    svc = request.headers.get("x-service-token")
    if svc is not None:
        if service_token and secrets.compare_digest(svc, service_token):
            return IdentityPrincipal("service", "service:%s" % (request.headers.get("x-service-name") or "unknown"), None)
        logger.warning("identity_auth: token de servico RECUSADO rota=%s nome=%s%s", rota,
                       request.headers.get("x-service-name") or "-",
                       "" if service_token else " (PLUGHUB_CHANNEL_GATEWAY_SERVICE_TOKEN vazio no gateway)")
        raise HTTPException(status_code=401, detail="credencial de servico invalida")

    tok = bearer_from_header(request.headers.get("authorization"))
    claims = verify_user_jwt(tok, jwt_secret) if tok else None
    if not claims:
        raise HTTPException(status_code=401, detail="rota de identidade exige credencial")
    if not user_grants:
        logger.warning("identity_auth: usuario sub=%s em rota INTERNA %s", claims.get("sub"), rota)
        raise HTTPException(status_code=403, detail="rota interna: exige credencial de servico")
    if not any(abac_can(claims, m, f, a) for (m, f, a) in user_grants):
        logger.warning("identity_auth: NEGADO sub=%s rota=%s — sem %s", claims.get("sub"), rota,
                       " | ".join("%s.%s" % (m, f) for (m, f, _a) in user_grants))
        raise HTTPException(status_code=403, detail="sem permissao para esta rota de identidade")
    tenant = str(claims.get("tenant_id") or "")
    if not tenant:
        raise HTTPException(status_code=403, detail="credencial sem tenant")
    return IdentityPrincipal("user", str(claims.get("sub") or ""), tenant, claims)


def tenant_for(principal: IdentityPrincipal, requested: str | None) -> str:
    """O tenant que vale para o pedido: o do JWT para usuário; o pedido para serviço."""
    if principal.kind == "user":
        if requested and requested != principal.tenant_id:
            logger.warning("identity_auth: sub=%s pediu tenant=%s com credencial de %s",
                           principal.sub, requested, principal.tenant_id)
            raise HTTPException(status_code=403, detail="tenant do pedido diverge do da credencial")
        return principal.tenant_id or ""
    if not requested:
        raise HTTPException(status_code=422, detail="tenant_id obrigatorio para chamador de servico")
    return requested
