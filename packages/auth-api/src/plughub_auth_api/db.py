"""
db.py
Schema PostgreSQL e CRUD via asyncpg.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from .jwt_utils import hash_refresh_token

logger = logging.getLogger("plughub.auth_api.db")

# ─── DDL ──────────────────────────────────────────────────────────────────────

DDL_SCHEMA = "CREATE SCHEMA IF NOT EXISTS auth"

DDL_USERS = """
CREATE TABLE IF NOT EXISTS auth.users (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        TEXT        NOT NULL,
    email            TEXT        NOT NULL,
    name             TEXT        NOT NULL DEFAULT '',
    password_hash    TEXT        NOT NULL,
    roles            TEXT[]      NOT NULL DEFAULT '{}',
    accessible_pools TEXT[]      NOT NULL DEFAULT '{}',
    active           BOOL        NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)
)
"""

DDL_SESSIONS = """
CREATE TABLE IF NOT EXISTS auth.sessions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    tenant_id           TEXT        NOT NULL,
    refresh_token_hash  TEXT        NOT NULL UNIQUE,
    expires_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

DDL_SESSIONS_IDX_USER = "CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON auth.sessions (user_id)"
DDL_SESSIONS_IDX_EXP  = "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON auth.sessions (expires_at)"

# ── Module registry — módulos declaram schema de permissões ───────────────────

DDL_MODULE_REGISTRY = """
CREATE TABLE IF NOT EXISTS auth.module_registry (
    module_id     TEXT        PRIMARY KEY,
    tenant_id     TEXT,
    label         TEXT        NOT NULL,
    icon          TEXT        NOT NULL DEFAULT '📦',
    nav_path      TEXT        NOT NULL DEFAULT '',
    schema        JSONB       NOT NULL DEFAULT '{}',
    active        BOOL        NOT NULL DEFAULT TRUE,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# ── Idempotent migrations para colunas adicionadas após criação inicial ────────

DDL_MIGRATE_USERS_MODULE_CONFIG = """
ALTER TABLE auth.users
    ADD COLUMN IF NOT EXISTS module_config JSONB NOT NULL DEFAULT '{}'
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(DDL_SCHEMA)
            await conn.execute(DDL_USERS)
            await conn.execute(DDL_SESSIONS)
            await conn.execute(DDL_SESSIONS_IDX_USER)
            await conn.execute(DDL_SESSIONS_IDX_EXP)
            await conn.execute(DDL_MODULE_REGISTRY)
            await conn.execute(DDL_MIGRATE_USERS_MODULE_CONFIG)
    logger.info("auth schema ensured")


# ─── User CRUD ────────────────────────────────────────────────────────────────

async def create_user(
    pool: asyncpg.Pool,
    tenant_id: str,
    email: str,
    password_hash: str,
    name: str,
    roles: list[str],
    accessible_pools: list[str],
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO auth.users (tenant_id, email, password_hash, name, roles, accessible_pools)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, tenant_id, email, name, roles, accessible_pools, active, created_at, updated_at
        """,
        tenant_id, email, password_hash, name, roles, accessible_pools,
    )
    return dict(row)


def _parse_module_config(row: dict[str, Any]) -> dict[str, Any]:
    """Normaliza module_config de JSONB/str para dict Python."""
    import json
    cfg = row.get("module_config")
    if cfg is None:
        row["module_config"] = {}
    elif isinstance(cfg, str):
        row["module_config"] = json.loads(cfg)
    return row


async def get_user_by_email(
    pool: asyncpg.Pool,
    tenant_id: str,
    email: str,
) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM auth.users WHERE tenant_id = $1 AND email = $2",
        tenant_id, email,
    )
    return _parse_module_config(dict(row)) if row else None


async def get_user_by_id(
    pool: asyncpg.Pool,
    user_id: str,
) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM auth.users WHERE id = $1",
        uuid.UUID(user_id),
    )
    return _parse_module_config(dict(row)) if row else None


async def list_users(
    pool: asyncpg.Pool,
    tenant_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, tenant_id, email, name, roles, accessible_pools,
               module_config, active, created_at, updated_at
        FROM auth.users
        WHERE tenant_id = $1
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
        """,
        tenant_id, limit, offset,
    )
    return [_parse_module_config(dict(r)) for r in rows]


async def update_user(
    pool: asyncpg.Pool,
    user_id: str,
    *,
    name: str | None = None,
    password_hash: str | None = None,
    roles: list[str] | None = None,
    accessible_pools: list[str] | None = None,
    active: bool | None = None,
) -> dict[str, Any] | None:
    sets = []
    params: list[Any] = []
    i = 1

    if name is not None:
        sets.append(f"name = ${i}"); params.append(name); i += 1
    if password_hash is not None:
        sets.append(f"password_hash = ${i}"); params.append(password_hash); i += 1
    if roles is not None:
        sets.append(f"roles = ${i}"); params.append(roles); i += 1
    if accessible_pools is not None:
        sets.append(f"accessible_pools = ${i}"); params.append(accessible_pools); i += 1
    if active is not None:
        sets.append(f"active = ${i}"); params.append(active); i += 1

    if not sets:
        return await get_user_by_id(pool, user_id)

    sets.append(f"updated_at = now()")
    params.append(uuid.UUID(user_id))

    row = await pool.fetchrow(
        f"""
        UPDATE auth.users SET {", ".join(sets)}
        WHERE id = ${i}
        RETURNING id, tenant_id, email, name, roles, accessible_pools,
                  module_config, active, created_at, updated_at
        """,
        *params,
    )
    return _parse_module_config(dict(row)) if row else None


async def delete_user(pool: asyncpg.Pool, user_id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM auth.users WHERE id = $1",
        uuid.UUID(user_id),
    )
    return result.endswith("1")


# ─── Session (refresh token) CRUD ─────────────────────────────────────────────

async def create_session(
    pool: asyncpg.Pool,
    user_id: str,
    tenant_id: str,
    refresh_token_hash: str,
    expire_days: int,
) -> str:
    """Cria sessão e retorna o session_id."""
    expires_at = datetime.now(timezone.utc) + timedelta(days=expire_days)
    row = await pool.fetchrow(
        """
        INSERT INTO auth.sessions (user_id, tenant_id, refresh_token_hash, expires_at)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        uuid.UUID(user_id), tenant_id, refresh_token_hash, expires_at,
    )
    return str(row["id"])


async def get_session_by_token_hash(
    pool: asyncpg.Pool,
    token_hash: str,
) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM auth.sessions WHERE refresh_token_hash = $1 AND expires_at > now()",
        token_hash,
    )
    return dict(row) if row else None


async def rotate_session(
    pool: asyncpg.Pool,
    old_token_hash: str,
    new_token_hash: str,
    expire_days: int,
) -> bool:
    """Troca o refresh_token_hash e renova expires_at. Retorna False se não encontrado."""
    expires_at = datetime.now(timezone.utc) + timedelta(days=expire_days)
    result = await pool.execute(
        """
        UPDATE auth.sessions
        SET refresh_token_hash = $1, expires_at = $2, last_used_at = now()
        WHERE refresh_token_hash = $3 AND expires_at > now()
        """,
        new_token_hash, expires_at, old_token_hash,
    )
    return result.endswith("1")


async def delete_session(pool: asyncpg.Pool, token_hash: str) -> bool:
    result = await pool.execute(
        "DELETE FROM auth.sessions WHERE refresh_token_hash = $1",
        token_hash,
    )
    return result.endswith("1")


async def delete_expired_sessions(pool: asyncpg.Pool) -> int:
    result = await pool.execute("DELETE FROM auth.sessions WHERE expires_at <= now()")
    # result = "DELETE N"
    try:
        return int(result.split()[-1])
    except (IndexError, ValueError):
        return 0


# ─── Module registry CRUD ─────────────────────────────────────────────────────

async def upsert_module(
    pool: asyncpg.Pool,
    module_id: str,
    label: str,
    icon: str,
    nav_path: str,
    schema: dict[str, Any],
    tenant_id: str | None = None,
    active: bool = True,
) -> dict[str, Any]:
    """Registra ou atualiza um módulo (upsert por module_id)."""
    import json
    row = await pool.fetchrow(
        """
        INSERT INTO auth.module_registry
            (module_id, tenant_id, label, icon, nav_path, schema, active, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, now())
        ON CONFLICT (module_id) DO UPDATE SET
            label      = EXCLUDED.label,
            icon       = EXCLUDED.icon,
            nav_path   = EXCLUDED.nav_path,
            schema     = EXCLUDED.schema,
            active     = EXCLUDED.active,
            updated_at = now()
        RETURNING *
        """,
        module_id, tenant_id, label, icon, nav_path,
        json.dumps(schema), active,
    )
    return dict(row)


async def get_module(pool: asyncpg.Pool, module_id: str) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM auth.module_registry WHERE module_id = $1",
        module_id,
    )
    return dict(row) if row else None


async def list_modules(
    pool: asyncpg.Pool,
    tenant_id: str | None = None,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """Lista módulos. tenant_id=None devolve apenas módulos de plataforma (tenant_id IS NULL)."""
    if active_only:
        rows = await pool.fetch(
            """
            SELECT * FROM auth.module_registry
            WHERE active = TRUE
              AND (tenant_id IS NULL OR tenant_id = $1)
            ORDER BY module_id
            """,
            tenant_id,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM auth.module_registry
            WHERE tenant_id IS NULL OR tenant_id = $1
            ORDER BY module_id
            """,
            tenant_id,
        )
    return [dict(r) for r in rows]


async def set_module_active(pool: asyncpg.Pool, module_id: str, active: bool) -> bool:
    result = await pool.execute(
        "UPDATE auth.module_registry SET active = $1, updated_at = now() WHERE module_id = $2",
        active, module_id,
    )
    return result.endswith("1")


# ─── module_config CRUD (no usuário) ──────────────────────────────────────────

async def get_user_module_config(
    pool: asyncpg.Pool,
    user_id: str,
) -> dict[str, Any]:
    """Retorna o module_config completo do usuário (dict vazio se não configurado)."""
    import json
    row = await pool.fetchrow(
        "SELECT module_config FROM auth.users WHERE id = $1",
        uuid.UUID(user_id),
    )
    if not row:
        return {}
    cfg = row["module_config"]
    return json.loads(cfg) if isinstance(cfg, str) else (cfg or {})


async def set_user_module_config(
    pool: asyncpg.Pool,
    user_id: str,
    module_config: dict[str, Any],
) -> bool:
    """Substitui todo o module_config do usuário."""
    import json
    result = await pool.execute(
        "UPDATE auth.users SET module_config = $1::jsonb, updated_at = now() WHERE id = $2",
        json.dumps(module_config), uuid.UUID(user_id),
    )
    return result.endswith("1")


async def patch_user_module_config(
    pool: asyncpg.Pool,
    user_id: str,
    module_id: str,
    module_data: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Atualiza apenas as chaves de um módulo específico dentro de module_config.
    Usa jsonb_set para merge parcial sem sobrescrever outros módulos.
    """
    import json
    row = await pool.fetchrow(
        """
        UPDATE auth.users
        SET module_config = jsonb_set(
            COALESCE(module_config, '{}'),
            ARRAY[$1],
            $2::jsonb,
            true
        ),
        updated_at = now()
        WHERE id = $3
        RETURNING id, tenant_id, email, name, roles, accessible_pools,
                  module_config, active, created_at, updated_at
        """,
        module_id,
        json.dumps(module_data),
        uuid.UUID(user_id),
    )
    if not row:
        return None
    d = dict(row)
    cfg = d.get("module_config")
    d["module_config"] = json.loads(cfg) if isinstance(cfg, str) else (cfg or {})
    return d


def validate_module_config(
    module_schema: dict[str, Any],
    config: dict[str, Any],
) -> list[str]:
    """
    Valida config contra o schema do módulo.
    Retorna lista de erros (vazia = válido).

    Regras:
      - Cada key em config deve existir em module_schema.permission_schema
      - O valor de 'access' deve estar no domain declarado
      - Se scopable=False, scope deve ser []
      - Valores de scope devem seguir o formato 'pool:<id>' ou 'campaign:<id>'
    """
    errors: list[str] = []
    permission_schema: dict[str, Any] = module_schema.get("permission_schema", {})

    for key, entry in config.items():
        if key not in permission_schema:
            errors.append(f"Campo desconhecido: '{key}' não existe no schema do módulo")
            continue

        field_def = permission_schema[key]
        domain: list[str] = field_def.get("domain", [])
        scopable: bool = field_def.get("scopable", False)

        access = entry.get("access")
        scope = entry.get("scope", [])

        if access not in domain:
            errors.append(
                f"Campo '{key}': access='{access}' inválido. Valores aceitos: {domain}"
            )

        if not scopable and scope:
            errors.append(
                f"Campo '{key}' não suporta escopo (scopable=false) mas scope={scope} foi enviado"
            )

        if scopable and scope:
            scope_type = field_def.get("scope_type", "pool")
            for s in scope:
                if not s.startswith(f"{scope_type}:"):
                    errors.append(
                        f"Campo '{key}': valor de scope inválido '{s}'. "
                        f"Formato esperado: '{scope_type}:<id>'"
                    )

    return errors


# ─── Seed ─────────────────────────────────────────────────────────────────────

async def seed_admin_if_absent(
    pool: asyncpg.Pool,
    tenant_id: str,
    email: str,
    password_hash: str,
    name: str,
) -> bool:
    """Cria usuário admin se não existir. Retorna True se criou."""
    existing = await get_user_by_email(pool, tenant_id, email)
    if existing:
        return False
    await create_user(
        pool,
        tenant_id=tenant_id,
        email=email,
        password_hash=password_hash,
        name=name,
        roles=["admin", "developer"],
        accessible_pools=[],
    )
    logger.info("seed admin user created: %s @ %s", email, tenant_id)
    return True
