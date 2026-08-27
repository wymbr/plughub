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
    """
    True se `pool_id` está no domínio do chamador.

    Ordem dos ramos (passo 2 do plano `accessible_pools`, 2026-08-27) — a MESMA do
    `_resolve_scope` da analytics-api, de propósito: dois serviços que respondem
    diferente à pergunta "este pool está no meu domínio?" é como se paga um vazamento.

      1. lista não-vazia → decide a lista. O RESTRITIVO vence, sempre: um
         `unrestricted` setado por engano não pode ALARGAR o domínio de um operador
         escopado, porque alargamento não aparece na tela como erro.
      2. claim `unrestricted` = true → irrestrito EXPLÍCITO.
      3. senão → irrestrito LEGADO (`[] = todos`), **contado**. Este ramo tem de
         sobreviver ao passo 2 inteiro: token vive 1h, então tokens sem o claim
         circulam depois do deploy. Ele desaparece no passo 3.
    """
    ap = accessible_pools(payload)
    if ap:
        return pool_id in ap
    if payload.get("unrestricted") is True:
        return True
    logger.warning(
        "pool_in_scope: irrestrito por LEGADO_POOLS_VAZIO — accessible_pools vazio e "
        "sem claim `unrestricted`. claim_presente=%s sub=%s pool=%s",
        "unrestricted" in payload, payload.get("sub", ""), pool_id,
    )
    return True


def bearer_from_header(authorization: str | None) -> str | None:
    """Extrai o token de um header `Authorization: Bearer <jwt>`. None se ausente/malformado."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None
