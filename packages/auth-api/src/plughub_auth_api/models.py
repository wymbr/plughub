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
    accessible_pools: list[str] = []   # [] = todos os pools (LEGADO — ver `unrestricted`)
    unrestricted: bool = False         # True = sem recorte de pool (ve o tenant inteiro)
    max_concurrent_sessions: int = Field(default=3, ge=1, le=50)


class UpdateUserRequest(BaseModel):
    name: str | None = None
    password: str | None = Field(default=None, min_length=8)
    roles: list[Role] | None = None
    accessible_pools: list[str] | None = None
    unrestricted: bool | None = None
    active: bool | None = None
    max_concurrent_sessions: int | None = Field(default=None, ge=1, le=50)


# ─── Responses ────────────────────────────────────────────────────────────────

class TokenUserInfo(BaseModel):
    id: str
    email: str
    name: str
    roles: list[str]
    tenant_id: str
    accessible_pools: list[str]
    # Declaração EXPLÍCITA de "sem recorte". Viaja no payload de login desde 2026-08-27
    # porque a UI monta a sessão daqui, e com o portão de navegação grant-first o claim
    # é a única porta larga que resta — ausente aqui, ele não existiria na tela.
    unrestricted: bool = False
    max_concurrent_sessions: int = 3     # capacity shared across all pools
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
    unrestricted: bool = False
    max_concurrent_sessions: int = 3
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
    max_concurrent_sessions: int = 3


# ─── Permissions & Templates ───────────────────────────────────────────────────

class CreateTemplateRequest(BaseModel):
    tenant_id: str
    name: str
    description: str = ""
    # Fase 1 (preset copy-on-create): snapshot rico do cadastro de usuário
    # {role, module_config, accessible_pools, max_concurrent_sessions}.
    config: dict = {}


class UpdateTemplateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict | None = None


class TemplateResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str
    config: dict
    created_at: str
    updated_at: str


