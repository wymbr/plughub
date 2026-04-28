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


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(DDL_SCHEMA)
        await conn.execute(DDL_USERS)
        await conn.execute(DDL_SESSIONS)
        await conn.execute(DDL_SESSIONS_IDX_USER)
        await conn.execute(DDL_SESSIONS_IDX_EXP)
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


async def get_user_by_email(
    pool: asyncpg.Pool,
    tenant_id: str,
    email: str,
) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM auth.users WHERE tenant_id = $1 AND email = $2",
        tenant_id, email,
    )
    return dict(row) if row else None


async def get_user_by_id(
    pool: asyncpg.Pool,
    user_id: str,
) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM auth.users WHERE id = $1",
        uuid.UUID(user_id),
    )
    return dict(row) if row else None


async def list_users(
    pool: asyncpg.Pool,
    tenant_id: str,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """
        SELECT id, tenant_id, email, name, roles, accessible_pools, active, created_at, updated_at
        FROM auth.users
        WHERE tenant_id = $1
        ORDER BY created_at DESC
        LIMIT $2 OFFSET $3
        """,
        tenant_id, limit, offset,
    )
    return [dict(r) for r in rows]


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
        RETURNING id, tenant_id, email, name, roles, accessible_pools, active, created_at, updated_at
        """,
        *params,
    )
    return dict(row) if row else None


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
