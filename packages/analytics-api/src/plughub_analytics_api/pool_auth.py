"""
pool_auth.py
Optional FastAPI dependency for pool-scoped data visibility (Arc 7c).

Reads an auth-api Bearer JWT (HS256) and extracts ``accessible_pools[]``
from its claims to restrict analytics queries to the caller's allowed pools.

Behaviour summary
-----------------
- analytics_open_access=True OR no auth_jwt_secret configured
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
    Lightweight identity object carrying pool-scoped access information.

    accessible_pools:
      None       → no restriction (all pools visible)
      list[str]  → caller may only see data for these pool_ids
    """

    def __init__(
        self,
        accessible_pools: list[str] | None,
        tenant_id: str | None,
        sub: str,
    ) -> None:
        self.accessible_pools = accessible_pools
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

    # Open-access mode or no JWT secret configured → unrestricted
    if settings.analytics_open_access or not settings.auth_jwt_secret:
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

    return PoolPrincipal(
        accessible_pools=accessible_pools,
        tenant_id=tenant_id,
        sub=sub,
    )
