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
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

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
    """FastAPI dependency — decodes and validates the Bearer JWT."""
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
