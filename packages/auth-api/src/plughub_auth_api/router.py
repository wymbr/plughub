"""
router.py
Endpoints REST da auth-api.

Autenticação de operações admin: header X-Admin-Token.
Autenticação de sessão (me/refresh/logout): header Authorization: Bearer <access_token>
                                            ou body refresh_token.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from jose import JWTError

from . import db as db_mod
from . import permissions as perms_mod
from .config import Settings, get_settings
from .jwt_utils import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from .models import (
    ApplyTemplateRequest,
    CreateTemplateRequest,
    CreateUserRequest,
    GrantPermissionRequest,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    PermissionResponse,
    RefreshRequest,
    ResolvePermissionResponse,
    TemplateResponse,
    TokenResponse,
    UpdateTemplateRequest,
    UpdateUserRequest,
    UserResponse,
)
from .password import hash_password, verify_password

logger = logging.getLogger("plughub.auth_api.router")

router = APIRouter(prefix="/auth", tags=["auth"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def _settings() -> Settings:
    return get_settings()


def _require_admin(
    x_admin_token: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(_settings),
) -> None:
    if settings.admin_token and x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Invalid admin token")


def _user_to_response(row: dict[str, Any]) -> UserResponse:
    return UserResponse(
        id=str(row["id"]),
        tenant_id=row["tenant_id"],
        email=row["email"],
        name=row["name"],
        roles=list(row["roles"]),
        accessible_pools=list(row["accessible_pools"]),
        active=row["active"],
        created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
        updated_at=row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
    )


def _make_token_response(
    user: dict[str, Any],
    settings: Settings,
) -> tuple[TokenResponse, str]:
    """Gera access_token + refresh_token. Retorna (TokenResponse, plain_refresh_token)."""
    plain_refresh = generate_refresh_token()
    access = create_access_token(
        user_id=str(user["id"]),
        tenant_id=user["tenant_id"],
        email=user["email"],
        name=user["name"],
        roles=list(user["roles"]),
        accessible_pools=list(user["accessible_pools"]),
        settings=settings,
    )
    expires_in = settings.access_token_expire_minutes * 60
    return (
        TokenResponse(
            access_token=access,
            refresh_token=plain_refresh,
            expires_in=expires_in,
        ),
        plain_refresh,
    )


async def _bearer_claims(request: Request, settings: Settings) -> dict[str, Any]:
    """Extrai e valida o Bearer token do cabeçalho Authorization."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[len("Bearer "):]
    try:
        return decode_access_token(token, settings)
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc


# ─── Auth endpoints ────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request) -> TokenResponse:
    """
    Login com e-mail e senha.
    Retorna access_token (JWT) + refresh_token (opaque, rotacionado).
    """
    pool = _get_pool(request)
    settings = _settings()

    user = await db_mod.get_user_by_email(pool, body.tenant_id, body.email)
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user["active"]:
        raise HTTPException(status_code=403, detail="User account is inactive")

    token_resp, plain_refresh = _make_token_response(user, settings)
    await db_mod.create_session(
        pool,
        user_id=str(user["id"]),
        tenant_id=user["tenant_id"],
        refresh_token_hash=hash_refresh_token(plain_refresh),
        expire_days=settings.refresh_token_expire_days,
    )
    logger.info("login ok: %s @ %s", user["email"], user["tenant_id"])
    return token_resp


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, request: Request) -> TokenResponse:
    """
    Troca o refresh_token por um novo par access+refresh (token rotation).
    O refresh_token antigo é invalidado imediatamente.
    """
    pool = _get_pool(request)
    settings = _settings()

    old_hash = hash_refresh_token(body.refresh_token)
    session = await db_mod.get_session_by_token_hash(pool, old_hash)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = await db_mod.get_user_by_id(pool, str(session["user_id"]))
    if not user or not user["active"]:
        raise HTTPException(status_code=403, detail="User account is inactive")

    token_resp, plain_refresh = _make_token_response(user, settings)
    rotated = await db_mod.rotate_session(
        pool,
        old_token_hash=old_hash,
        new_token_hash=hash_refresh_token(plain_refresh),
        expire_days=settings.refresh_token_expire_days,
    )
    if not rotated:
        raise HTTPException(status_code=409, detail="Token rotation conflict — try again")

    return token_resp


@router.post("/logout", status_code=204)
async def logout(body: LogoutRequest, request: Request) -> None:
    """Invalida o refresh_token. Idempotente (sem erro se não encontrado)."""
    pool = _get_pool(request)
    token_hash = hash_refresh_token(body.refresh_token)
    await db_mod.delete_session(pool, token_hash)


@router.get("/me", response_model=MeResponse)
async def me(request: Request) -> MeResponse:
    """Retorna as claims do access token Bearer presente no header."""
    settings = _settings()
    claims = await _bearer_claims(request, settings)
    return MeResponse(
        sub=claims["sub"],
        tenant_id=claims["tenant_id"],
        email=claims["email"],
        name=claims["name"],
        roles=claims["roles"],
        accessible_pools=claims["accessible_pools"],
    )


# ─── User management (admin) ──────────────────────────────────────────────────

@router.post("/users", response_model=UserResponse, status_code=201,
             dependencies=[Depends(_require_admin)])
async def create_user(body: CreateUserRequest, request: Request) -> UserResponse:
    pool = _get_pool(request)
    # Verifica se e-mail já existe
    existing = await db_mod.get_user_by_email(pool, body.tenant_id, body.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered in this tenant")

    row = await db_mod.create_user(
        pool,
        tenant_id=body.tenant_id,
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        roles=body.roles,
        accessible_pools=body.accessible_pools,
    )
    return _user_to_response(row)


@router.get("/users", response_model=list[UserResponse],
            dependencies=[Depends(_require_admin)])
async def list_users(
    request: Request,
    tenant_id: str = "tenant_demo",
    limit: int = 100,
    offset: int = 0,
) -> list[UserResponse]:
    pool = _get_pool(request)
    rows = await db_mod.list_users(pool, tenant_id, limit=limit, offset=offset)
    return [_user_to_response(r) for r in rows]


@router.get("/users/{user_id}", response_model=UserResponse,
            dependencies=[Depends(_require_admin)])
async def get_user(user_id: str, request: Request) -> UserResponse:
    pool = _get_pool(request)
    row = await db_mod.get_user_by_id(pool, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_response(row)


@router.patch("/users/{user_id}", response_model=UserResponse,
              dependencies=[Depends(_require_admin)])
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    request: Request,
) -> UserResponse:
    pool = _get_pool(request)
    # Garante que o usuário existe
    existing = await db_mod.get_user_by_id(pool, user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="User not found")

    ph = hash_password(body.password) if body.password else None
    row = await db_mod.update_user(
        pool,
        user_id=user_id,
        name=body.name,
        password_hash=ph,
        roles=body.roles,
        accessible_pools=body.accessible_pools,
        active=body.active,
    )
    return _user_to_response(row)


@router.delete("/users/{user_id}", status_code=204,
               dependencies=[Depends(_require_admin)])
async def delete_user(user_id: str, request: Request) -> None:
    pool = _get_pool(request)
    deleted = await db_mod.delete_user(pool, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")


# ─── Platform permissions (admin) ─────────────────────────────────────────────

def _perm_to_response(row: dict[str, Any]) -> PermissionResponse:
    return PermissionResponse(
        id=str(row["id"]),
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        module=row["module"],
        action=row["action"],
        scope_type=row["scope_type"],
        scope_id=row.get("scope_id"),
        granted_by=row["granted_by"],
        template_id=str(row["template_id"]) if row.get("template_id") else None,
        created_at=row["created_at"].isoformat() if hasattr(row.get("created_at"), "isoformat") else str(row.get("created_at", "")),
    )


@router.post("/permissions", response_model=PermissionResponse, status_code=201,
             dependencies=[Depends(_require_admin)])
async def grant_permission(body: GrantPermissionRequest, request: Request) -> PermissionResponse:
    """Concede permissão a um usuário. Idempotente (ON CONFLICT UPDATE)."""
    pool = _get_pool(request)
    row = await perms_mod.grant_permission(
        pool,
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        module=body.module,
        action=body.action,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        granted_by=body.granted_by,
    )
    return _perm_to_response(row)


@router.get("/permissions", response_model=list[PermissionResponse],
            dependencies=[Depends(_require_admin)])
async def list_permissions(
    request: Request,
    tenant_id: str = "tenant_demo",
    user_id: str | None = None,
    module: str | None = None,
) -> list[PermissionResponse]:
    pool = _get_pool(request)
    rows = await perms_mod.list_permissions(pool, tenant_id, user_id=user_id, module=module)
    return [_perm_to_response(r) for r in rows]


@router.delete("/permissions/{permission_id}", status_code=204,
               dependencies=[Depends(_require_admin)])
async def revoke_permission(permission_id: str, request: Request) -> None:
    pool = _get_pool(request)
    revoked = await perms_mod.revoke_permission(pool, permission_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Permission not found")


@router.get("/permissions/resolve", response_model=ResolvePermissionResponse)
async def resolve_permission(
    request: Request,
    tenant_id: str,
    user_id: str,
    module: str,
    action: str,
    pool_id: str | None = None,
) -> ResolvePermissionResponse:
    """
    Verifica se o usuário tem permissão para (module, action) no escopo indicado.
    Acessível sem admin token — útil para UIs verificarem permissões antes de renderizar.
    """
    pool = _get_pool(request)
    allowed = await perms_mod.resolve_permissions(
        pool, tenant_id=tenant_id, user_id=user_id,
        module=module, action=action, pool_id=pool_id,
    )
    return ResolvePermissionResponse(
        allowed=allowed, user_id=user_id, module=module, action=action, pool_id=pool_id,
    )


# ─── Permission templates (admin) ─────────────────────────────────────────────

def _tmpl_to_response(row: dict[str, Any]) -> TemplateResponse:
    import json as _json
    perms = row["permissions"]
    if isinstance(perms, str):
        perms = _json.loads(perms)
    return TemplateResponse(
        id=str(row["id"]),
        tenant_id=row["tenant_id"],
        name=row["name"],
        description=row.get("description", ""),
        permissions=perms if isinstance(perms, list) else [],
        created_at=row["created_at"].isoformat() if hasattr(row.get("created_at"), "isoformat") else str(row.get("created_at", "")),
        updated_at=row["updated_at"].isoformat() if hasattr(row.get("updated_at"), "isoformat") else str(row.get("updated_at", "")),
    )


@router.post("/templates", response_model=TemplateResponse, status_code=201,
             dependencies=[Depends(_require_admin)])
async def create_template(body: CreateTemplateRequest, request: Request) -> TemplateResponse:
    pool = _get_pool(request)
    perms_list = [p.model_dump() for p in body.permissions]
    row = await perms_mod.create_template(
        pool,
        tenant_id=body.tenant_id,
        name=body.name,
        description=body.description,
        permissions=perms_list,
    )
    return _tmpl_to_response(row)


@router.get("/templates", response_model=list[TemplateResponse],
            dependencies=[Depends(_require_admin)])
async def list_templates(
    request: Request,
    tenant_id: str = "tenant_demo",
) -> list[TemplateResponse]:
    pool = _get_pool(request)
    rows = await perms_mod.list_templates(pool, tenant_id)
    return [_tmpl_to_response(r) for r in rows]


@router.get("/templates/{template_id}", response_model=TemplateResponse,
            dependencies=[Depends(_require_admin)])
async def get_template(template_id: str, request: Request) -> TemplateResponse:
    pool = _get_pool(request)
    row = await perms_mod.get_template(pool, template_id)
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    return _tmpl_to_response(row)


@router.patch("/templates/{template_id}", response_model=TemplateResponse,
              dependencies=[Depends(_require_admin)])
async def update_template(
    template_id: str,
    body: UpdateTemplateRequest,
    request: Request,
) -> TemplateResponse:
    pool = _get_pool(request)
    existing = await perms_mod.get_template(pool, template_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Template not found")
    perms_list = [p.model_dump() for p in body.permissions] if body.permissions is not None else None
    row = await perms_mod.update_template(
        pool, template_id,
        name=body.name, description=body.description, permissions=perms_list,
    )
    return _tmpl_to_response(row)


@router.delete("/templates/{template_id}", status_code=204,
               dependencies=[Depends(_require_admin)])
async def delete_template(template_id: str, request: Request) -> None:
    pool = _get_pool(request)
    deleted = await perms_mod.delete_template(pool, template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")


@router.post("/templates/{template_id}/apply", response_model=list[PermissionResponse],
             dependencies=[Depends(_require_admin)])
async def apply_template(
    template_id: str,
    body: ApplyTemplateRequest,
    request: Request,
) -> list[PermissionResponse]:
    """
    Aplica o template a um usuário, materializando as permissões em platform_permissions.
    scope_override sobrescreve o scope_type/scope_id de todas as entradas do template.
    """
    pool = _get_pool(request)
    try:
        rows = await perms_mod.apply_template(
            pool,
            template_id=template_id,
            tenant_id=body.user_id.split(":")[0] if ":" in body.user_id else "tenant_demo",
            user_id=body.user_id,
            granted_by=body.granted_by,
            scope_override=body.scope_override,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_perm_to_response(r) for r in rows]
