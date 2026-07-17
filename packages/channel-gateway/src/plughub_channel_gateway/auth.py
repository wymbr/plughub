"""
auth.py
Verificação de JWT de USUÁRIO do auth-api (HS256, module_config ABAC) no
channel-gateway.

Padrão COMPARTILHÁVEL: usa o mesmo segredo `PLUGHUB_AUTH_JWT_SECRET` que a
analytics-api consome (settings.auth_jwt_secret). Outros módulos que precisarem
autenticar usuários do auth-api devem reusar estas funções em vez de reimplementar
o decode/ABAC.

Uso (A5 — resume de aprovação interna):
    payload = verify_user_jwt(token, settings.auth_jwt_secret)
    if payload and abac_can(payload, "approvals", "decide", "write_only"):
        ...  # possessed-grade; `sub` = decided_by
"""
from __future__ import annotations

import logging

import jwt as pyjwt

logger = logging.getLogger("plughub.channel-gateway.auth")

# Espelha o _ACCESS_ORDER da analytics-api / PermissionChecker (platform-ui).
# read_only e write_only colapsam em 1; read_write = 2.
_ACCESS_ORDER = {"none": 0, "read_only": 1, "write_only": 1, "read_write": 2}


def verify_user_jwt(token: str | None, secret: str) -> dict | None:
    """
    Decodifica+valida o JWT de usuário do auth-api (HS256). Retorna o payload ou
    None quando o token é ausente/inválido/expirado, ou quando o segredo não está
    configurado (verificação desabilitada → o chamador cai no caminho externo).
    """
    if not token or not secret:
        return None
    try:
        return pyjwt.decode(token, secret, algorithms=["HS256"])
    except pyjwt.ExpiredSignatureError:
        logger.info("auth: user JWT expired")
        return None
    except pyjwt.InvalidTokenError as exc:
        logger.info("auth: user JWT invalid: %s", exc)
        return None


def abac_can(
    payload: dict, module: str, field: str, min_access: str = "read_only"
) -> bool:
    """
    True se module_config[module][field].access >= min_access.
    Degradação graciosa: contas legadas sem `module_config` → False (sem acesso).
    """
    mc = payload.get("module_config") or {}
    mod = mc.get(module) or {}
    fld = mod.get(field) or {}
    access = fld.get("access", "none") if isinstance(fld, dict) else "none"
    return _ACCESS_ORDER.get(access, 0) >= _ACCESS_ORDER.get(min_access, 1)


def accessible_pools(payload: dict) -> list[str]:
    """Lista de pools do JWT (Arc 7). Vazia = todos os pools (sem restrição)."""
    ap = payload.get("accessible_pools")
    return ap if isinstance(ap, list) else []


def pool_in_scope(payload: dict, pool_id: str) -> bool:
    """True se `pool_id` está em accessible_pools, ou se a lista é vazia (= todos)."""
    ap = accessible_pools(payload)
    return (not ap) or (pool_id in ap)


def bearer_from_header(authorization: str | None) -> str | None:
    """Extrai o token de um header `Authorization: Bearer <jwt>`. None se ausente/malformado."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None
