"""
auth.py
FastAPI dependency for admin endpoint RBAC.

Two roles:
  admin    — can query all tenants; no tenant_id restriction
  operator — restricted to a single tenant_id embedded in the JWT

JWT format (HS256, secret from settings.admin_jwt_secret):
  {
    "sub":       "user@example.com",
    "role":      "admin" | "operator",
    "tenant_id": "tenant_telco"   ← required when role == "operator"
  }

Usage:
  @router.get("/admin/consolidated")
  async def endpoint(principal: Principal = Depends(require_principal)):
      ...
"""
from __future__ import annotations

import logging

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings
from .pool_auth import raw_bearer_from_request

logger = logging.getLogger("plughub.analytics.auth")

_bearer = HTTPBearer(auto_error=False)


class Principal:
    """Decoded identity from a verified JWT."""

    def __init__(self, role: str, tenant_id: str | None, sub: str) -> None:
        self.role      = role
        self.tenant_id = tenant_id
        self.sub       = sub

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_operator(self) -> bool:
        return self.role == "operator"

    def effective_tenant(self, requested: str | None) -> str | None:
        """
        Returns the tenant that should be applied to a query.
          - admin:    returns `requested` as-is (may be None → all tenants)
          - operator: always returns their own tenant_id (ignores `requested`)
        """
        if self.is_admin:
            return requested
        return self.tenant_id


async def require_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """FastAPI dependency — decodes and validates the Bearer JWT.

    When PLUGHUB_ANALYTICS_OPEN_ACCESS=true (demo / dev), returns an admin
    principal without requiring a token.  Never enable in production.
    """
    settings = get_settings()
    if settings.analytics_open_access:
        return Principal(role="admin", tenant_id=None, sub="open_access")

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    settings = get_settings()
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.admin_jwt_secret,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    return _principal_from_system_payload(payload)


def _principal_from_system_payload(payload: dict) -> Principal:
    """Claims do token de SISTEMA -> `Principal`. Uma casa so, porque
    `require_principal` (/admin/*) e `require_dashboard_principal` decidem o mesmo."""
    role      = payload.get("role", "operator")
    tenant_id = payload.get("tenant_id")
    sub       = payload.get("sub", "")

    if role not in ("admin", "operator"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Unknown role: {role!r}",
        )
    if role == "operator" and not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator token must include tenant_id",
        )

    return Principal(role=role, tenant_id=tenant_id, sub=sub)


# ==============================================================================
# `/dashboard/*` -- o verificador que aceita TAMBEM o JWT de usuario
# ==============================================================================
#
# MEDIDO em 2026-08-28. O analytics-api tem DOIS verificadores com segredos
# diferentes, e ate aqui as quatro rotas de `/dashboard/*` estavam na porta errada:
#
#   `require_principal`  -> `admin_jwt_secret`  (token de SISTEMA, /admin/*)
#   `pool_auth.*`        -> `auth_jwt_secret`   (JWT do auth-api, o do login)
#
# O navegador so tem o segundo. Enquanto `analytics_open_access` era `true` isso nao
# aparecia: o bypass devolvia um principal `admin` antes de qualquer decode. Ao
# endurecer o demo (2026-08-27, `PLUGHUB_ANALYTICS_OPEN_ACCESS` default `false`), as
# quatro rotas ficaram INALCANCAVEIS pela UI -- nem pelo cabecalho nem pelo `?token=`,
# porque nao era origem do token, era SEGREDO. Sintoma: Monitor > Sessions e os
# cartoes de pool do Console vazios, com o 401 engolido pelo `catch` do hook.
#
# Por que uma dependencia NOVA em vez de alargar `require_principal`: aquele guarda
# tambem `/admin/*`, superficie de SISTEMA (cross-tenant, flush, backfill). Alargar la
# daria a qualquer usuario logado o que hoje exige o segredo de sistema -- e
# alargamento e o erro que nao aparece na tela, porque so mostra dado a MAIS.
#
# Ordem das tentativas e o que cada uma concede:
#   1. `admin_jwt_secret` -> semantica de HOJE, intacta (admin pode cross-tenant).
#   2. `auth_jwt_secret`  -> usuario logado, SEMPRE preso ao proprio tenant
#      (`role="operator"`), mesmo com `roles: ["admin"]` no token. Papel de produto
#      nao e papel de sistema; conceder cross-tenant por causa dele seria ler dois
#      fatos no mesmo campo.
#
# Expiracao NAO cai para a tentativa seguinte: assinatura conferida + `exp` vencido ja
# identifica o emissor, e tentar o outro segredo transformaria "expirado" (que a UI
# resolve com refresh) em "invalido" (que ela trata como erro de credencial).


def _principal_from_user_jwt(payload: dict) -> Principal:
    """JWT do auth-api -> `Principal` preso ao tenant do proprio token."""
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        # Recusa nomeada: sem tenant no token nao da para dizer QUAL tenant ele le, e
        # `effective_tenant` de um operator sem tenant devolveria None, que os handlers
        # leem como "todos". Campo ausente virando escopo total e exatamente o valor
        # plausivel que esta casa persegue.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user token must include tenant_id",
        )
    return Principal(role="operator", tenant_id=tenant_id, sub=payload.get("sub", ""))


async def require_dashboard_principal(request: Request) -> Principal:
    """
    Identidade para `/dashboard/*`: aceita o token de SISTEMA (`admin_jwt_secret`) OU o
    JWT do usuario logado (`auth_jwt_secret`), vindo do cabecalho ou de `?token=`.

    O `?token=` nao e preferencia de estilo: `/dashboard/operational` e SSE, e
    `EventSource` nao envia cabecalho. A leitura das duas origens e a MESMA funcao que
    o escopo de pool usa (`pool_auth.raw_bearer_from_request`), para nao existir um
    endpoint que autorize por uma origem e escope por outra.

    Recusas nomeadas -- quem depura precisa distinguir falta de config no SERVICO de
    falta de token no CHAMADOR:
      `auth_required`  -- nenhum token, em nenhuma das duas origens.
      `Token expired`  -- assinatura confere, `exp` vencido (a UI resolve com refresh).
      `Invalid token`  -- nao confere com nenhum dos dois segredos.
    """
    settings = get_settings()
    if settings.analytics_open_access:
        return Principal(role="admin", tenant_id=None, sub="open_access")

    raw = raw_bearer_from_request(request)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="auth_required",
        )

    # -- 1. token de sistema --------------------------------------------------
    payload = None
    if settings.admin_jwt_secret:
        try:
            payload = jwt.decode(raw, settings.admin_jwt_secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired",
            )
        except jwt.InvalidTokenError:
            payload = None
    if payload is not None:
        return _principal_from_system_payload(payload)

    # -- 2. JWT do usuario (auth-api) -----------------------------------------
    if not settings.auth_jwt_secret:
        logger.error(
            "require_dashboard_principal: `auth_jwt_secret` AUSENTE -- o JWT do usuario "
            "nao tem como ser verificado, entao so o token de SISTEMA abre "
            "/dashboard/*. A UI recebe 401 em Monitor e Console."
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token",
        )
    try:
        payload = jwt.decode(raw, settings.auth_jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired",
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("dashboard JWT invalido nos DOIS segredos: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token",
        )
    return _principal_from_user_jwt(payload)
