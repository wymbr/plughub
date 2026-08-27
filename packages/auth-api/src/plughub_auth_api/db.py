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
    unrestricted     BOOL        NOT NULL DEFAULT FALSE,
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

DDL_MIGRATE_USERS_MAX_CONCURRENT = """
ALTER TABLE auth.users
    ADD COLUMN IF NOT EXISTS max_concurrent_sessions INT NOT NULL DEFAULT 3
"""

# ── Passo 2 do plano `accessible_pools` (2026-08-27) ───────────────────────────
# `accessible_pools = []` significa HOJE "todos os pools" — convencao implicita, lida
# por SETE tradutores em servicos diferentes. O passo 3 inverte esse significado para
# "nenhum pool", e sem um jeito EXPLICITO de dizer "este usuario nao tem recorte" a
# inversao apagaria o acesso de quem depende da convencao, em silencio.
#
# `unrestricted` e essa declaracao. Default FALSE de proposito: NAO ha backfill.
# Backfillar converteria em concessao declarada aquilo que hoje pode ser acidente (a
# coluna nasce `'{}'` por default), e na direcao irreversivel. Em vez disso, cada
# tradutor CONTA quando resolve irrestrito pelo caminho legado — e o passo 3 decide
# com a lista na mao, nao com esperanca.
DDL_MIGRATE_USERS_UNRESTRICTED = """
ALTER TABLE auth.users
    ADD COLUMN IF NOT EXISTS unrestricted BOOL NOT NULL DEFAULT FALSE
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


# ── Language Cleanup — os TRES campos de `config` que ficaram para tras ────────
# `recursos->resources` e `mascaramento->masking` foram renomeados dos DOIS lados
# (catalogo + leitores). `plataforma`/`canais`/`usuarios` so mudaram na UI, entao os
# itens de menu gateados em `config.platform|users|channels` pediam campos que o
# catalogo NAO definia: nenhum grant podia satisfaze-los, e eles passavam apenas pelo
# bypass de papel. Mesmo padrao idempotente das tres migracoes acima — o `WHERE` so
# casa a linha que ainda carrega o nome velho.
DDL_MIGRATE_ABAC_PLATAFORMA = """
UPDATE auth.users
SET module_config = jsonb_set(
    module_config, '{config}',
    ((module_config -> 'config') - 'plataforma')
      || jsonb_build_object('platform', module_config -> 'config' -> 'plataforma')
)
WHERE (module_config -> 'config') ? 'plataforma'
"""

DDL_MIGRATE_ABAC_CANAIS = """
UPDATE auth.users
SET module_config = jsonb_set(
    module_config, '{config}',
    ((module_config -> 'config') - 'canais')
      || jsonb_build_object('channels', module_config -> 'config' -> 'canais')
)
WHERE (module_config -> 'config') ? 'canais'
"""

DDL_MIGRATE_ABAC_USUARIOS = """
UPDATE auth.users
SET module_config = jsonb_set(
    module_config, '{config}',
    ((module_config -> 'config') - 'usuarios')
      || jsonb_build_object('users', module_config -> 'config' -> 'usuarios')
)
WHERE (module_config -> 'config') ? 'usuarios'
"""

# Split `config.users` -> `users` + `permissions` (2026-08-27).
#
# ⚠️ ESTE backfill e legitimo, e o do `unrestricted` nao era — a diferenca importa.
# La, inferir a intencao a partir da ausencia teria ALARGADO escopo por adivinhacao
# ("nao tem lista, entao pode tudo"). Aqui o campo esta sendo PARTIDO: quem tem
# `config.users` HOJE ja pode conceder, porque as duas capacidades moram no mesmo
# campo. Copiar a metade nova preserva exatamente o que ja era verdade; NAO copiar
# e que seria a mudanca silenciosa — o admin perderia a tela de Acesso no deploy.
#
# `read_only` em `users` vira `read_only` em `permissions`: quem so lia continua so
# lendo. So o `read_write` carrega o poder de conceder.
DDL_MIGRATE_ABAC_PERMISSIONS = """
UPDATE auth.users
SET module_config = jsonb_set(
    module_config, '{config}',
    (module_config -> 'config')
      || jsonb_build_object('permissions', module_config -> 'config' -> 'users')
)
WHERE (module_config -> 'config') ? 'users'
  AND NOT ((module_config -> 'config') ? 'permissions')
"""

# Passo 2 (2026-08-27): `config.platform` era catch-all de cinco telas. Os campos
# novos sao recortados DELE, entao quem ja o tinha continua alcancando as mesmas
# telas — mesmo raciocinio do split `users`/`permissions`: preservar o que ja era
# verdade, nao inferir intencao a partir de ausencia. Sem isto o admin perderia
# Dashboards, Calendars e DialogForms no deploy.
DDL_MIGRATE_ABAC_PLATFORM_SPLIT = """
UPDATE auth.users
SET module_config = jsonb_set(
    module_config, '{config}',
    (module_config -> 'config')
      || jsonb_build_object(
           'dashboards',   module_config -> 'config' -> 'platform',
           'calendars',    module_config -> 'config' -> 'platform',
           'dialog_forms', module_config -> 'config' -> 'platform')
)
WHERE (module_config -> 'config') ? 'platform'
  AND NOT ((module_config -> 'config') ? 'dashboards')
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
            await conn.execute(DDL_MIGRATE_USERS_MAX_CONCURRENT)
            await conn.execute(DDL_MIGRATE_USERS_UNRESTRICTED)
            # Language Cleanup Phase 2 — rename Portuguese ABAC field names
            await conn.execute(DDL_MIGRATE_ABAC_RELATORIO)
            await conn.execute(DDL_MIGRATE_ABAC_RECURSOS)
            await conn.execute(DDL_MIGRATE_ABAC_MASCARAMENTO)
            await conn.execute(DDL_MIGRATE_ABAC_PLATAFORMA)
            await conn.execute(DDL_MIGRATE_ABAC_CANAIS)
            await conn.execute(DDL_MIGRATE_ABAC_USUARIOS)
            await conn.execute(DDL_MIGRATE_ABAC_PERMISSIONS)
            await conn.execute(DDL_MIGRATE_ABAC_PLATFORM_SPLIT)
            # Arc 9 — Agent Groups (member/shift tables removed 2026-07-02 — see
            # docs/arcos/arc9-agent-groups.md; tables may still exist physically
            # in older DBs, just no longer created/read/written by this service)
            await conn.execute(DDL_AGENT_GROUPS)
            await conn.execute(DDL_AGENT_GROUP_USERS)
            await conn.execute(DDL_AGENT_GROUP_SUPERVISORS)
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
    max_concurrent_sessions: int = 3,
    unrestricted: bool = False,
) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        INSERT INTO auth.users
            (tenant_id, email, password_hash, name, roles, accessible_pools,
             max_concurrent_sessions, unrestricted)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id, tenant_id, email, name, roles, accessible_pools, unrestricted,
                  max_concurrent_sessions, active, created_at, updated_at
        """,
        tenant_id, email, password_hash, name, roles, accessible_pools,
        max_concurrent_sessions, unrestricted,
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
        SELECT id, tenant_id, email, name, roles, accessible_pools, unrestricted,
               module_config, max_concurrent_sessions, active, created_at, updated_at
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
    max_concurrent_sessions: int | None = None,
    unrestricted: bool | None = None,
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
    if max_concurrent_sessions is not None:
        sets.append(f"max_concurrent_sessions = ${i}"); params.append(max_concurrent_sessions); i += 1
    if unrestricted is not None:
        sets.append(f"unrestricted = ${i}"); params.append(unrestricted); i += 1

    if not sets:
        return await get_user_by_id(pool, user_id)

    sets.append(f"updated_at = now()")
    params.append(uuid.UUID(user_id))

    row = await pool.fetchrow(
        f"""
        UPDATE auth.users SET {", ".join(sets)}
        WHERE id = ${i}
        RETURNING id, tenant_id, email, name, roles, accessible_pools, unrestricted,
                  module_config, max_concurrent_sessions, active, created_at, updated_at
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
               COUNT(DISTINCT m.user_id) AS member_count,
               COUNT(DISTINCT s.user_id) AS supervisor_count
        FROM auth.agent_groups AS g
        LEFT JOIN auth.agent_group_users       AS m ON m.group_id = g.group_id
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


# ── Supervisor scope resolution (called at login/refresh) ─────────────────────

async def resolve_supervisor_scope(
    pool: asyncpg.Pool,
    user_id: str,
    role: str,
) -> tuple[list[str], list[str]]:
    """
    Returns (supervised_groups, supervised_user_ids).

    Algorithm:
      1. Find all groups where user is a supervisor.
      2. Expand those groups → user_ids (group members).
      3. Admin role always gets [] (no restriction).

    Note (2026-07-02): shift-based time-windowing and the agent_type_id
    expansion (Arc 9 original design) were removed — see
    docs/arcos/arc9-agent-groups.md. Differing shift needs are now modeled as
    separate groups instead of per-member time windows, and Pool.agent_kind
    is the single source of truth for human/AI typing (previously duplicated,
    unvalidated, in agent_group_members). Supervisor scope is membership-only;
    pool-level row scoping is handled separately by `accessible_pools` (Arc 7).
    """
    if role == "admin":
        return [], []

    uid = uuid.UUID(user_id)
    sup_rows = await pool.fetch(
        "SELECT group_id FROM auth.agent_group_supervisors WHERE user_id = $1",
        uid,
    )
    if not sup_rows:
        return [], []

    group_ids = [row["group_id"] for row in sup_rows]

    user_rows = await pool.fetch(
        "SELECT DISTINCT user_id FROM auth.agent_group_users WHERE group_id = ANY($1::uuid[])",
        group_ids,
    )
    user_ids = [str(r["user_id"]) for r in user_rows]

    group_ids_str = [str(g) for g in group_ids]
    return group_ids_str, user_ids


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
        unrestricted=True,
    )
    logger.info("seed admin user created: %s @ %s", email, tenant_id)
    return True
