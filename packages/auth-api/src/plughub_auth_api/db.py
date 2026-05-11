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

# ── Language Cleanup Phase 2 — rename Portuguese ABAC field keys in module_config
# Each UPDATE is idempotent: the WHERE clause only matches rows that still carry
# the old key name, so re-running on an already-migrated DB is a no-op.

DDL_MIGRATE_ABAC_RELATORIO = """
UPDATE auth.users
SET module_config = jsonb_set(
    module_config,
    '{evaluation}',
    ((module_config -> 'evaluation') - 'relatorio')
      || jsonb_build_object('report', module_config -> 'evaluation' -> 'relatorio')
)
WHERE (module_config -> 'evaluation') ? 'relatorio'
"""

DDL_MIGRATE_ABAC_RECURSOS = """
UPDATE auth.users
SET module_config = jsonb_set(
    module_config,
    '{config}',
    ((module_config -> 'config') - 'recursos')
      || jsonb_build_object('resources', module_config -> 'config' -> 'recursos')
)
WHERE (module_config -> 'config') ? 'recursos'
"""

DDL_MIGRATE_ABAC_MASCARAMENTO = """
UPDATE auth.users
SET module_config = jsonb_set(
    module_config,
    '{config}',
    ((module_config -> 'config') - 'mascaramento')
      || jsonb_build_object('masking', module_config -> 'config' -> 'mascaramento')
)
WHERE (module_config -> 'config') ? 'mascaramento'
"""

# ── Arc 9 — Agent Groups & Supervisor Scope ───────────────────────────────────

DDL_AGENT_GROUPS = """
CREATE TABLE IF NOT EXISTS auth.agent_groups (
    group_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT        NOT NULL,
    name        TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

DDL_AGENT_GROUP_MEMBERS = """
CREATE TABLE IF NOT EXISTS auth.agent_group_members (
    group_id      UUID    NOT NULL REFERENCES auth.agent_groups(group_id) ON DELETE CASCADE,
    agent_type_id TEXT    NOT NULL,
    is_human      BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (group_id, agent_type_id)
)
"""

DDL_AGENT_GROUP_USERS = """
CREATE TABLE IF NOT EXISTS auth.agent_group_users (
    group_id UUID NOT NULL REFERENCES auth.agent_groups(group_id) ON DELETE CASCADE,
    user_id  UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, user_id)
)
"""

DDL_AGENT_GROUP_SUPERVISORS = """
CREATE TABLE IF NOT EXISTS auth.agent_group_supervisors (
    group_id UUID NOT NULL REFERENCES auth.agent_groups(group_id) ON DELETE CASCADE,
    user_id  UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    PRIMARY KEY (group_id, user_id)
)
"""

DDL_AGENT_GROUP_SHIFTS = """
CREATE TABLE IF NOT EXISTS auth.agent_group_shifts (
    shift_id           UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id           UUID    NOT NULL REFERENCES auth.agent_groups(group_id) ON DELETE CASCADE,
    supervisor_user_id UUID    NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    days_of_week       INT[]   NOT NULL DEFAULT '{}',
    time_start         TIME    NOT NULL,
    time_end           TIME    NOT NULL,
    timezone           TEXT    NOT NULL DEFAULT 'UTC',
    active             BOOLEAN NOT NULL DEFAULT true
)
"""

DDL_AGENT_GROUPS_IDX_TENANT = "CREATE INDEX IF NOT EXISTS idx_agent_groups_tenant ON auth.agent_groups (tenant_id)"


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
            # Language Cleanup Phase 2 — rename Portuguese ABAC field names
            await conn.execute(DDL_MIGRATE_ABAC_RELATORIO)
            await conn.execute(DDL_MIGRATE_ABAC_RECURSOS)
            await conn.execute(DDL_MIGRATE_ABAC_MASCARAMENTO)
            # Arc 9 — Agent Groups
            await conn.execute(DDL_AGENT_GROUPS)
            await conn.execute(DDL_AGENT_GROUP_MEMBERS)
            await conn.execute(DDL_AGENT_GROUP_USERS)
            await conn.execute(DDL_AGENT_GROUP_SUPERVISORS)
            await conn.execute(DDL_AGENT_GROUP_SHIFTS)
            await conn.execute(DDL_AGENT_GROUPS_IDX_TENANT)
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


# ─── Arc 9 — Agent Group CRUD ─────────────────────────────────────────────────

async def create_group(
    pool: asyncpg.Pool,
    tenant_id: str,
    name: str,
    description: str = "",
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO auth.agent_groups (tenant_id, name, description)
        VALUES ($1, $2, $3)
        RETURNING group_id, tenant_id, name, description, created_at, updated_at
        """,
        tenant_id, name, description,
    )
    return dict(row)


async def list_groups(
    pool: asyncpg.Pool,
    tenant_id: str,
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT g.group_id, g.tenant_id, g.name, g.description, g.created_at, g.updated_at,
               COUNT(DISTINCT m.agent_type_id) AS member_count,
               COUNT(DISTINCT s.user_id)        AS supervisor_count
        FROM auth.agent_groups AS g
        LEFT JOIN auth.agent_group_members    AS m ON m.group_id = g.group_id
        LEFT JOIN auth.agent_group_supervisors AS s ON s.group_id = g.group_id
        WHERE g.tenant_id = $1
        GROUP BY g.group_id, g.tenant_id, g.name, g.description, g.created_at, g.updated_at
        ORDER BY g.name
        """,
        tenant_id,
    )
    return [dict(r) for r in rows]


async def get_group(
    pool: asyncpg.Pool,
    group_id: str,
) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM auth.agent_groups WHERE group_id = $1",
        uuid.UUID(group_id),
    )
    return dict(row) if row else None


async def update_group(
    pool: asyncpg.Pool,
    group_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
) -> dict[str, Any] | None:
    sets, params = [], []
    i = 1
    if name is not None:
        sets.append(f"name = ${i}"); params.append(name); i += 1
    if description is not None:
        sets.append(f"description = ${i}"); params.append(description); i += 1
    if not sets:
        return await get_group(pool, group_id)
    sets.append("updated_at = now()")
    params.append(uuid.UUID(group_id))
    row = await pool.fetchrow(
        f"UPDATE auth.agent_groups SET {', '.join(sets)} WHERE group_id = ${i} "
        f"RETURNING group_id, tenant_id, name, description, created_at, updated_at",
        *params,
    )
    return dict(row) if row else None


async def delete_group(pool: asyncpg.Pool, group_id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM auth.agent_groups WHERE group_id = $1",
        uuid.UUID(group_id),
    )
    return result.endswith("1")


# ── Group members ──────────────────────────────────────────────────────────────

async def add_group_member(
    pool: asyncpg.Pool,
    group_id: str,
    agent_type_id: str,
    is_human: bool = False,
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO auth.agent_group_members (group_id, agent_type_id, is_human)
        VALUES ($1, $2, $3)
        ON CONFLICT (group_id, agent_type_id) DO UPDATE SET is_human = EXCLUDED.is_human
        RETURNING group_id, agent_type_id, is_human
        """,
        uuid.UUID(group_id), agent_type_id, is_human,
    )
    return dict(row)


async def remove_group_member(
    pool: asyncpg.Pool,
    group_id: str,
    agent_type_id: str,
) -> bool:
    result = await pool.execute(
        "DELETE FROM auth.agent_group_members WHERE group_id = $1 AND agent_type_id = $2",
        uuid.UUID(group_id), agent_type_id,
    )
    return result.endswith("1")


async def list_group_members(
    pool: asyncpg.Pool,
    group_id: str,
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT group_id, agent_type_id, is_human FROM auth.agent_group_members WHERE group_id = $1 ORDER BY agent_type_id",
        uuid.UUID(group_id),
    )
    return [dict(r) for r in rows]


# ── Group users (human agents) ─────────────────────────────────────────────────

async def add_group_user(
    pool: asyncpg.Pool,
    group_id: str,
    user_id: str,
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO auth.agent_group_users (group_id, user_id)
        VALUES ($1, $2)
        ON CONFLICT (group_id, user_id) DO NOTHING
        RETURNING group_id, user_id
        """,
        uuid.UUID(group_id), uuid.UUID(user_id),
    )
    return dict(row) if row else {"group_id": group_id, "user_id": user_id}


async def remove_group_user(
    pool: asyncpg.Pool,
    group_id: str,
    user_id: str,
) -> bool:
    result = await pool.execute(
        "DELETE FROM auth.agent_group_users WHERE group_id = $1 AND user_id = $2",
        uuid.UUID(group_id), uuid.UUID(user_id),
    )
    return result.endswith("1")


async def list_group_users(
    pool: asyncpg.Pool,
    group_id: str,
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT u.id, u.email, u.name, u.roles
        FROM auth.agent_group_users AS gu
        JOIN auth.users AS u ON u.id = gu.user_id
        WHERE gu.group_id = $1
        ORDER BY u.name
        """,
        uuid.UUID(group_id),
    )
    return [dict(r) for r in rows]


# ── Group supervisors ──────────────────────────────────────────────────────────

async def add_group_supervisor(
    pool: asyncpg.Pool,
    group_id: str,
    user_id: str,
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO auth.agent_group_supervisors (group_id, user_id)
        VALUES ($1, $2)
        ON CONFLICT (group_id, user_id) DO NOTHING
        RETURNING group_id, user_id
        """,
        uuid.UUID(group_id), uuid.UUID(user_id),
    )
    return dict(row) if row else {"group_id": group_id, "user_id": user_id}


async def remove_group_supervisor(
    pool: asyncpg.Pool,
    group_id: str,
    user_id: str,
) -> bool:
    result = await pool.execute(
        "DELETE FROM auth.agent_group_supervisors WHERE group_id = $1 AND user_id = $2",
        uuid.UUID(group_id), uuid.UUID(user_id),
    )
    return result.endswith("1")


async def list_group_supervisors(
    pool: asyncpg.Pool,
    group_id: str,
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT u.id, u.email, u.name, u.roles
        FROM auth.agent_group_supervisors AS gs
        JOIN auth.users AS u ON u.id = gs.user_id
        WHERE gs.group_id = $1
        ORDER BY u.name
        """,
        uuid.UUID(group_id),
    )
    return [dict(r) for r in rows]


# ── Group shifts ───────────────────────────────────────────────────────────────

async def create_group_shift(
    pool: asyncpg.Pool,
    group_id: str,
    supervisor_user_id: str,
    days_of_week: list[int],
    time_start: str,
    time_end: str,
    timezone: str = "UTC",
    active: bool = True,
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO auth.agent_group_shifts
            (group_id, supervisor_user_id, days_of_week, time_start, time_end, timezone, active)
        VALUES ($1, $2, $3, $4::time, $5::time, $6, $7)
        RETURNING shift_id, group_id, supervisor_user_id, days_of_week,
                  time_start, time_end, timezone, active
        """,
        uuid.UUID(group_id), uuid.UUID(supervisor_user_id),
        days_of_week, time_start, time_end, timezone, active,
    )
    return _serialize_shift(dict(row))


async def update_group_shift(
    pool: asyncpg.Pool,
    shift_id: str,
    *,
    supervisor_user_id: str | None = None,
    days_of_week: list[int] | None = None,
    time_start: str | None = None,
    time_end: str | None = None,
    timezone: str | None = None,
    active: bool | None = None,
) -> dict[str, Any] | None:
    sets, params = [], []
    i = 1
    if supervisor_user_id is not None:
        sets.append(f"supervisor_user_id = ${i}"); params.append(uuid.UUID(supervisor_user_id)); i += 1
    if days_of_week is not None:
        sets.append(f"days_of_week = ${i}"); params.append(days_of_week); i += 1
    if time_start is not None:
        sets.append(f"time_start = ${i}::time"); params.append(time_start); i += 1
    if time_end is not None:
        sets.append(f"time_end = ${i}::time"); params.append(time_end); i += 1
    if timezone is not None:
        sets.append(f"timezone = ${i}"); params.append(timezone); i += 1
    if active is not None:
        sets.append(f"active = ${i}"); params.append(active); i += 1
    if not sets:
        row = await pool.fetchrow(
            "SELECT * FROM auth.agent_group_shifts WHERE shift_id = $1",
            uuid.UUID(shift_id),
        )
        return _serialize_shift(dict(row)) if row else None
    params.append(uuid.UUID(shift_id))
    row = await pool.fetchrow(
        f"UPDATE auth.agent_group_shifts SET {', '.join(sets)} WHERE shift_id = ${i} "
        f"RETURNING shift_id, group_id, supervisor_user_id, days_of_week, time_start, time_end, timezone, active",
        *params,
    )
    return _serialize_shift(dict(row)) if row else None


async def delete_group_shift(pool: asyncpg.Pool, shift_id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM auth.agent_group_shifts WHERE shift_id = $1",
        uuid.UUID(shift_id),
    )
    return result.endswith("1")


async def list_group_shifts(
    pool: asyncpg.Pool,
    group_id: str,
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT shift_id, group_id, supervisor_user_id, days_of_week, time_start, time_end, timezone, active "
        "FROM auth.agent_group_shifts WHERE group_id = $1 ORDER BY time_start",
        uuid.UUID(group_id),
    )
    return [_serialize_shift(dict(r)) for r in rows]


def _serialize_shift(row: dict[str, Any]) -> dict[str, Any]:
    """Serialize TIME columns to HH:MM:SS strings for JSON safety."""
    import datetime as _dt
    for key in ("time_start", "time_end"):
        v = row.get(key)
        if isinstance(v, _dt.time):
            row[key] = v.strftime("%H:%M:%S")
    for key in ("group_id", "shift_id", "supervisor_user_id"):
        if key in row and row[key] is not None:
            row[key] = str(row[key])
    return row


# ── Supervisor scope resolution (called at login/refresh) ─────────────────────

async def resolve_supervisor_scope(
    pool: asyncpg.Pool,
    user_id: str,
    role: str,
) -> tuple[list[str], list[str], list[str]]:
    """
    Returns (supervised_groups, supervised_agent_types, supervised_user_ids).

    Algorithm (per spec):
      1. Find all groups where user is a supervisor.
      2. For each group: if shifts exist for this user → include only if a shift is
         currently active (day_of_week match + time window in shift.timezone).
         If no shifts exist for this user in the group → always include.
      3. Expand included groups → agent_type_ids (members) and user_ids (group users).
      4. Admin role always gets [] (no restriction).
    """
    import datetime as _dt
    import zoneinfo

    if role == "admin":
        return [], [], []

    uid = uuid.UUID(user_id)
    # Step 1: groups supervised by this user
    sup_rows = await pool.fetch(
        "SELECT group_id FROM auth.agent_group_supervisors WHERE user_id = $1",
        uid,
    )
    if not sup_rows:
        return [], [], []

    now_utc = _dt.datetime.now(_dt.timezone.utc)
    active_group_ids: list[uuid.UUID] = []

    for row in sup_rows:
        gid = row["group_id"]
        # Check if this group has shifts for this user
        shift_rows = await pool.fetch(
            """
            SELECT days_of_week, time_start, time_end, timezone
            FROM auth.agent_group_shifts
            WHERE group_id = $1 AND supervisor_user_id = $2 AND active = true
            """,
            gid, uid,
        )
        if not shift_rows:
            # No shifts for this user in this group → always active
            active_group_ids.append(gid)
            continue
        # Check if any shift is currently active
        is_active = False
        for s in shift_rows:
            try:
                tz = zoneinfo.ZoneInfo(s["timezone"] or "UTC")
            except Exception:
                tz = _dt.timezone.utc
            now_local = now_utc.astimezone(tz)
            dow = now_local.weekday()  # 0=Mon…6=Sun; spec uses 0=Sun…6=Sat
            # Convert spec convention (0=Sun) to Python (0=Mon)
            spec_dow = (dow + 1) % 7
            if spec_dow not in (s["days_of_week"] or []):
                continue
            ts = s["time_start"]
            te = s["time_end"]
            now_t = now_local.time().replace(tzinfo=None)
            if isinstance(ts, _dt.time):
                ts = ts.replace(tzinfo=None)
            if isinstance(te, _dt.time):
                te = te.replace(tzinfo=None)
            if ts <= now_t <= te:
                is_active = True
                break
        if is_active:
            active_group_ids.append(gid)

    if not active_group_ids:
        # Supervisor has groups configured but none are active right now.
        # Return non-empty lists with a sentinel that matches nothing to avoid
        # the "empty list = no restriction" branch; use a dummy value.
        return ["__no_active_shift__"], ["__no_active_shift__"], ["__no_active_shift__"]

    # Step 3: expand agent_types
    member_rows = await pool.fetch(
        "SELECT DISTINCT agent_type_id FROM auth.agent_group_members WHERE group_id = ANY($1::uuid[])",
        active_group_ids,
    )
    agent_types = [r["agent_type_id"] for r in member_rows]

    # Step 4: expand user_ids
    user_rows = await pool.fetch(
        "SELECT DISTINCT user_id FROM auth.agent_group_users WHERE group_id = ANY($1::uuid[])",
        active_group_ids,
    )
    user_ids = [str(r["user_id"]) for r in user_rows]

    group_ids_str = [str(g) for g in active_group_ids]
    return group_ids_str, agent_types, user_ids


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
