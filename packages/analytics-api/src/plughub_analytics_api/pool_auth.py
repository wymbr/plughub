"""
pool_auth.py
Optional FastAPI dependency for pool-scoped data visibility (Arc 7c).

Reads an auth-api Bearer JWT (HS256) and extracts ``accessible_pools[]``
from its claims to restrict analytics queries to the caller's allowed pools.

Behaviour summary
-----------------
NB (Segurança Fase A/E): pool-scoping é DESACOPLADO de ``analytics_open_access``. Aquele
flag é um bypass amplo de demo (audit/admin/transcript sem token) — mas o domínio de pools
deve valer sempre que houver como verificar o token. Aqui o único bypass é a AUSÊNCIA de
segredo (sem como verificar o JWT).
- No auth_jwt_secret configured
    → PoolPrincipal(accessible_pools=None) — no restriction (all pools)
- No Authorization header present
    → PoolPrincipal(accessible_pools=None) — unauthenticated callers see all pools
      (backward-compatible with existing dashboard/report consumers)
- Valid JWT, accessible_pools=[]   (auth-api convention for "all pools" / admin)
    → PoolPrincipal(accessible_pools=None) — no restriction
- Valid JWT, accessible_pools=[…]  (restricted operator)
    → PoolPrincipal(accessible_pools=[…]) — queries filtered to those pools only
- Invalid / expired JWT
    → HTTP 401

Usage
-----
    @router.get("/reports/sessions")
    async def report_sessions(
        ...,
        pool_principal: PoolPrincipal = Depends(optional_pool_principal),
    ):
        accessible = pool_principal.accessible_pools   # None | list[str]
        data = await query_sessions_report(..., accessible_pools=accessible)
"""
from __future__ import annotations

import logging

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

logger = logging.getLogger("plughub.analytics.pool_auth")

_bearer = HTTPBearer(auto_error=False)


class PoolPrincipal:
    """
    Lightweight identity object carrying pool-scoped and agent-group-scoped access.

    accessible_pools:
      None       → no restriction (all pools visible)
      list[str]  → caller may only see data for these pool_ids

    supervised_agent_types (Arc 9):
      None       → no restriction (all agent types visible)
      list[str]  → caller may only see sessions/segments involving these agent_type_ids
    """

    def __init__(
        self,
        accessible_pools: list[str] | None,
        tenant_id: str | None,
        sub: str,
        supervised_agent_types: list[str] | None = None,
    ) -> None:
        self.accessible_pools       = accessible_pools
        self.supervised_agent_types = supervised_agent_types
        self.tenant_id = tenant_id
        self.sub = sub

    @property
    def is_unrestricted(self) -> bool:
        """True when the caller can see all pools."""
        return self.accessible_pools is None


async def optional_pool_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> PoolPrincipal:
    """
    FastAPI dependency — optionally decodes an auth-api Bearer JWT.

    Always succeeds (never raises 401 for missing token). Raises 401 only
    when a token IS present but fails verification.
    """
    settings = get_settings()

    # Pool-scoping é DESACOPLADO do `analytics_open_access` (Segurança Fase A/E): o
    # open_access é um bypass amplo de demo (audit/admin/transcript sem token), mas o
    # domínio de pools deve valer sempre que dá p/ verificar o token. Único bypass aqui:
    # nenhum segredo configurado (não há como verificar o JWT) → irrestrito. Com segredo,
    # a decisão vem do token (ausente → irrestrito no path abaixo; presente → enforça).
    if not settings.auth_jwt_secret:
        return PoolPrincipal(accessible_pools=None, tenant_id=None, sub="open")

    # No token → unrestricted (backward-compatible with unauthenticated callers)
    if not credentials:
        return PoolPrincipal(accessible_pools=None, tenant_id=None, sub="anonymous")

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.auth_jwt_secret,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("pool_auth JWT validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    sub        = payload.get("sub", "")
    tenant_id  = payload.get("tenant_id")
    raw_pools  = payload.get("accessible_pools", [])  # [] = all pools in auth-api

    # auth-api convention: accessible_pools=[] means global access (admin/developer)
    accessible_pools: list[str] | None = None if not raw_pools else list(raw_pools)

    # Arc 9 — supervised_agent_types: [] = no restriction (admin); non-empty = filter
    raw_agent_types = payload.get("supervised_agent_types", [])
    supervised_agent_types: list[str] | None = None if not raw_agent_types else list(raw_agent_types)

    return PoolPrincipal(
        accessible_pools=accessible_pools,
        tenant_id=tenant_id,
        sub=sub,
        supervised_agent_types=supervised_agent_types,
    )


def accessible_pools_from_token(token: str | None) -> list[str] | None:
    """
    Decode `accessible_pools` from a raw JWT string passed as a QUERY PARAM.

    For SSE/EventSource callers (dashboard streams): the browser's EventSource cannot
    send an Authorization header, so the auth-api Bearer travels as `?token=`. Lenient
    by design — missing/invalid/expired token or `accessible_pools=[]` → None
    (unrestricted). NEVER raises: this is read-only pool-scoping that must degrade open
    (a bad token can't 401 a stream), mirroring the operational snapshot's posture.
    Decoupled from `analytics_open_access` (same as `optional_pool_principal`).
    """
    settings = get_settings()
    if not settings.auth_jwt_secret or not token:
        return None
    try:
        payload = jwt.decode(token, settings.auth_jwt_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    raw = payload.get("accessible_pools", [])
    return None if not raw else list(raw)
