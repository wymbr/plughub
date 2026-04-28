"""
permissions.py
DDL e CRUD para platform_permissions e permission_templates.

platform_permissions:
  Permissão explícita de um usuário a um módulo/ação com escopo opcional de pool.
  Uma linha por (user_id, module, action, scope_type, scope_id).

permission_templates:
  Conjunto nomeado de permissões reutilizável.
  Ao aplicar um template a um usuário (apply_template), as permissões são
  materializadas em platform_permissions — auditoria simples, sem lookup em cadeia.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

logger = logging.getLogger("plughub.auth_api.permissions")

# ─── Domínios ─────────────────────────────────────────────────────────────────

MODULES = {
    "analytics", "evaluation", "billing", "config",
    "registry", "skill_flows", "campaigns", "workflows", "*",
}

ACTIONS = {"view", "edit", "admin", "*"}
SCOPE_TYPES = {"pool", "global"}


# ─── DDL ──────────────────────────────────────────────────────────────────────

DDL_PERMISSIONS = """
CREATE TABLE IF NOT EXISTS auth.platform_permissions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT        NOT NULL,
    user_id     TEXT        NOT NULL,
    module      TEXT        NOT NULL,
    action      TEXT        NOT NULL,
    scope_type  TEXT        NOT NULL CHECK (scope_type IN ('pool', 'global')),
    scope_id    TEXT,
    granted_by  TEXT        NOT NULL DEFAULT 'system',
    template_id UUID,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id, module, action, scope_type, COALESCE(scope_id, ''))
)
"""

DDL_PERMISSIONS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_platform_perms_user "
    "ON auth.platform_permissions (tenant_id, user_id)"
)

DDL_TEMPLATES = """
CREATE TABLE IF NOT EXISTS auth.permission_templates (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT        NOT NULL,
    name        TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    permissions JSONB       NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
)
"""


async def ensure_permissions_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(DDL_PERMISSIONS)
        await conn.execute(DDL_PERMISSIONS_IDX)
        await conn.execute(DDL_TEMPLATES)
    logger.info("platform_permissions schema ensured")


# ─── platform_permissions CRUD ────────────────────────────────────────────────

async def grant_permission(
    pool: asyncpg.Pool,
    tenant_id: str,
    user_id: str,
    module: str,
    action: str,
    scope_type: str,
    scope_id: str | None,
    granted_by: str,
    template_id: str | None = None,
) -> dict[str, Any]:
    """
    Upsert de uma permissão. Retorna a linha resultante.
    Idempotente: conflito de UNIQUE → retorna a linha existente.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO auth.platform_permissions
            (tenant_id, user_id, module, action, scope_type, scope_id, granted_by, template_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (tenant_id, user_id, module, action, scope_type, COALESCE(scope_id, ''))
        DO UPDATE SET granted_by = EXCLUDED.granted_by, template_id = EXCLUDED.template_id
        RETURNING *
        """,
        tenant_id, user_id, module, action, scope_type, scope_id, granted_by,
        uuid.UUID(template_id) if template_id else None,
    )
    return dict(row)


async def revoke_permission(pool: asyncpg.Pool, permission_id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM auth.platform_permissions WHERE id = $1",
        uuid.UUID(permission_id),
    )
    return result.endswith("1")


async def list_permissions(
    pool: asyncpg.Pool,
    tenant_id: str,
    user_id: str | None = None,
    module: str | None = None,
) -> list[dict[str, Any]]:
    conditions = ["tenant_id = $1"]
    params: list[Any] = [tenant_id]
    i = 2
    if user_id is not None:
        conditions.append(f"user_id = ${i}"); params.append(user_id); i += 1
    if module is not None:
        conditions.append(f"module = ${i}"); params.append(module); i += 1

    rows = await pool.fetch(
        f"SELECT * FROM auth.platform_permissions WHERE {' AND '.join(conditions)} ORDER BY created_at",
        *params,
    )
    return [dict(r) for r in rows]


async def resolve_permissions(
    pool: asyncpg.Pool,
    tenant_id: str,
    user_id: str,
    module: str,
    action: str,
    pool_id: str | None = None,
) -> bool:
    """
    Retorna True se o usuário tem permissão para (module, action) no escopo indicado.

    Resolução (mais específico para mais geral, acumulativa):
      1. global   — scope_type='global'
      2. pool     — scope_type='pool' AND scope_id=$pool_id (se pool_id fornecido)

    Curingas:
      module='*' ou action='*' batem em qualquer valor pedido.
    """
    rows = await pool.fetch(
        """
        SELECT scope_type, scope_id FROM auth.platform_permissions
        WHERE tenant_id = $1
          AND user_id   = $2
          AND (module   = $3 OR module = '*')
          AND (action   = $4 OR action = '*')
        """,
        tenant_id, user_id, module, action,
    )
    for row in rows:
        if row["scope_type"] == "global":
            return True
        if row["scope_type"] == "pool" and pool_id and row["scope_id"] == pool_id:
            return True
    return False


async def get_accessible_pools_for_module(
    pool: asyncpg.Pool,
    tenant_id: str,
    user_id: str,
    module: str,
    action: str = "view",
) -> list[str] | None:
    """
    Retorna a lista de pool_ids acessíveis ao usuário para (module, action).
    Retorna None se o usuário tiver permissão global (acesso a todos os pools).
    Retorna [] se não tiver nenhuma permissão.
    """
    rows = await pool.fetch(
        """
        SELECT scope_type, scope_id FROM auth.platform_permissions
        WHERE tenant_id = $1
          AND user_id   = $2
          AND (module   = $3 OR module = '*')
          AND (action   = $4 OR action = '*')
        """,
        tenant_id, user_id, module, action,
    )
    pool_ids: list[str] = []
    for row in rows:
        if row["scope_type"] == "global":
            return None  # acesso irrestrito
        if row["scope_type"] == "pool" and row["scope_id"]:
            pool_ids.append(row["scope_id"])
    return pool_ids


# ─── permission_templates CRUD ────────────────────────────────────────────────

async def create_template(
    pool: asyncpg.Pool,
    tenant_id: str,
    name: str,
    description: str,
    permissions: list[dict[str, Any]],
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO auth.permission_templates (tenant_id, name, description, permissions)
        VALUES ($1, $2, $3, $4::jsonb)
        RETURNING *
        """,
        tenant_id, name, description, json.dumps(permissions),
    )
    return dict(row)


async def get_template(pool: asyncpg.Pool, template_id: str) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM auth.permission_templates WHERE id = $1",
        uuid.UUID(template_id),
    )
    return dict(row) if row else None


async def list_templates(
    pool: asyncpg.Pool,
    tenant_id: str,
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT * FROM auth.permission_templates WHERE tenant_id = $1 ORDER BY name",
        tenant_id,
    )
    return [dict(r) for r in rows]


async def update_template(
    pool: asyncpg.Pool,
    template_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    permissions: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    sets = []
    params: list[Any] = []
    i = 1

    if name is not None:
        sets.append(f"name = ${i}"); params.append(name); i += 1
    if description is not None:
        sets.append(f"description = ${i}"); params.append(description); i += 1
    if permissions is not None:
        sets.append(f"permissions = ${i}::jsonb"); params.append(json.dumps(permissions)); i += 1

    if not sets:
        return await get_template(pool, template_id)

    sets.append("updated_at = now()")
    params.append(uuid.UUID(template_id))

    row = await pool.fetchrow(
        f"UPDATE auth.permission_templates SET {', '.join(sets)} WHERE id = ${i} RETURNING *",
        *params,
    )
    return dict(row) if row else None


async def delete_template(pool: asyncpg.Pool, template_id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM auth.permission_templates WHERE id = $1",
        uuid.UUID(template_id),
    )
    return result.endswith("1")


async def apply_template(
    pool: asyncpg.Pool,
    template_id: str,
    tenant_id: str,
    user_id: str,
    granted_by: str,
    scope_override: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """
    Materializa as permissões do template em platform_permissions para o usuário.
    scope_override: {scope_type, scope_id} para sobrescrever o escopo do template.
    Retorna lista de permissões criadas/atualizadas.
    """
    template = await get_template(pool, template_id)
    if not template:
        raise ValueError(f"Template {template_id} not found")

    perms_def: list[dict[str, Any]] = template["permissions"]
    if isinstance(perms_def, str):
        perms_def = json.loads(perms_def)

    created: list[dict[str, Any]] = []
    for perm in perms_def:
        scope_type = scope_override.get("scope_type", perm.get("scope_type", "global")) if scope_override else perm.get("scope_type", "global")
        scope_id   = scope_override.get("scope_id",   perm.get("scope_id"))             if scope_override else perm.get("scope_id")

        row = await grant_permission(
            pool,
            tenant_id=tenant_id,
            user_id=user_id,
            module=perm["module"],
            action=perm["action"],
            scope_type=scope_type,
            scope_id=scope_id,
            granted_by=granted_by,
            template_id=template_id,
        )
        created.append(row)

    logger.info(
        "template %s applied to user %s — %d permissions materialized",
        template["name"], user_id, len(created),
    )
    return created
