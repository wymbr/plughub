"""
models.py
Pydantic schemas de entrada/saída da auth-api.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# ─── Roles ────────────────────────────────────────────────────────────────────

Role = Literal["operator", "supervisor", "admin", "developer", "business"]


# ─── Requests ─────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str
    tenant_id: str = "tenant_demo"


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class CreateUserRequest(BaseModel):
    tenant_id: str
    email: str
    password: str = Field(min_length=8)
    name: str = ""
    roles: list[Role] = ["operator"]
    accessible_pools: list[str] = []   # [] = todos os pools


class UpdateUserRequest(BaseModel):
    name: str | None = None
    password: str | None = Field(default=None, min_length=8)
    roles: list[Role] | None = None
    accessible_pools: list[str] | None = None
    active: bool | None = None


# ─── Responses ────────────────────────────────────────────────────────────────

class TokenUserInfo(BaseModel):
    id: str
    email: str
    name: str
    roles: list[str]
    tenant_id: str
    accessible_pools: list[str]
    module_config: dict[str, Any] = {}   # ABAC config por módulo (carregado do JWT)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int          # seconds
    user: TokenUserInfo


class UserResponse(BaseModel):
    id: str
    tenant_id: str
    email: str
    name: str
    roles: list[str]
    accessible_pools: list[str]
    active: bool
    created_at: str
    updated_at: str


class MeResponse(BaseModel):
    sub: str
    tenant_id: str
    email: str
    name: str
    roles: list[str]
    accessible_pools: list[str]
    module_config: dict[str, Any] = {}   # ABAC config por módulo


# ─── Permissions & Templates ───────────────────────────────────────────────────

class GrantPermissionRequest(BaseModel):
    tenant_id: str
    user_id: str
    module: str
    action: str
    scope_type: str = "global"   # "global" | "pool"
    scope_id: str | None = None  # pool_id for scope_type="pool"
    granted_by: str = "admin"


class PermissionResponse(BaseModel):
    id: str
    tenant_id: str
    user_id: str
    module: str
    action: str
    scope_type: str
    scope_id: str | None
    granted_by: str
    template_id: str | None
    created_at: str


class PermissionEntry(BaseModel):
    """Entrada de permissão dentro de um template."""
    module: str
    action: str
    scope_type: str = "global"
    scope_id: str | None = None


class CreateTemplateRequest(BaseModel):
    tenant_id: str
    name: str
    description: str = ""
    permissions: list[PermissionEntry] = []


class UpdateTemplateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    permissions: list[PermissionEntry] | None = None


class TemplateResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str
    permissions: list[dict]
    created_at: str
    updated_at: str


class ApplyTemplateRequest(BaseModel):
    user_id: str
    granted_by: str = "admin"
    scope_override: dict | None = None   # {"scope_type": "pool", "scope_id": "pool_xyz"}


class ResolvePermissionResponse(BaseModel):
    allowed: bool
    user_id: str
    module: str
    action: str
    pool_id: str | None
