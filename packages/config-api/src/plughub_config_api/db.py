"""
db.py
asyncpg DDL + raw database operations for the platform_config table.

Schema design:
  tenant_id = '__global__' is the sentinel for platform-wide defaults.
  Any real tenant_id overrides the global value for that (namespace, key).

  The UNIQUE constraint on (tenant_id, namespace, key) ensures clean upserts.

Two-level lookup (used by ConfigStore.get):
  SELECT value FROM platform_config
  WHERE tenant_id IN ($1, '__global__')
    AND namespace = $2 AND key = $3
  ORDER BY CASE WHEN tenant_id = $1 THEN 0 ELSE 1 END
  LIMIT 1
  → tenant-specific value wins over global; None if neither exists.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger("plughub.config.db")

GLOBAL = "__global__"

_DDL_GRANT = "GRANT CREATE, USAGE ON SCHEMA public TO CURRENT_USER"

_DDL_TABLE = """
CREATE TABLE IF NOT EXISTS public.platform_config (
    id          SERIAL PRIMARY KEY,
    tenant_id   TEXT        NOT NULL DEFAULT '__global__',
    namespace   TEXT        NOT NULL,
    key         TEXT        NOT NULL,
    value       JSONB       NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_platform_config UNIQUE (tenant_id, namespace, key)
)
"""

_DDL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_platform_config_lookup
    ON public.platform_config (tenant_id, namespace, key)
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    """Create the platform_config table and index if they do not exist.

    Uses an explicit transaction so asyncpg commits the DDL before releasing
    the connection back to the pool.  Without conn.transaction(), asyncpg may
    leave a server-side implicit transaction open; releasing the connection
    then triggers a silent rollback.

    Explicitly qualifies the table with the 'public' schema to avoid any
    search_path ambiguity — the default '$user' schema for user 'plughub'
    could shadow 'public' if a schema named 'plughub' exists.

    Verification uses a *fresh* pool connection acquired after the DDL
    connection is released, confirming the committed state is globally visible.
    """
    async with pool.acquire() as conn:
        db_name = await conn.fetchval("SELECT current_database()")
        logger.info("ensure_schema: running on database '%s'", db_name)

        # Attempt to ensure the current user has schema privileges.
        # IMPORTANT: This runs in auto-commit mode (outside the DDL transaction)
        # so that a GRANT failure does NOT poison the subsequent transaction.
        # This is a no-op for superusers (plughub is a superuser in the Docker
        # image) and ensures compatibility with PostgreSQL 15+ where CREATE on
        # 'public' is not granted to PUBLIC by default.
        try:
            await conn.execute(_DDL_GRANT)
            logger.info("ensure_schema: schema GRANT applied")
        except Exception as grant_exc:
            # Superusers bypass grant checks — a failure here is non-fatal.
            logger.warning("GRANT on public schema skipped (user is superuser or already privileged): %s", grant_exc)

        # Explicit transaction — guaranteed commit before conn is released.
        async with conn.transaction():
            # Pin search_path inside the transaction to eliminate any ambiguity
            # (SET LOCAL is transaction-scoped in PostgreSQL).
            await conn.execute("SET LOCAL search_path TO public")
            await conn.execute(_DDL_TABLE)
            await conn.execute(_DDL_INDEX)
        # Transaction committed; conn is still valid but no longer in a tx.
        logger.info("ensure_schema: DDL committed in database '%s'", db_name)

    # Re-acquire a fresh connection to verify the DDL is globally visible.
    exists = await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_tables"
        " WHERE schemaname = 'public' AND tablename = 'platform_config')"
    )
    if not exists:
        raise RuntimeError(
            "public.platform_config not found after CREATE TABLE IF NOT EXISTS — "
            "DDL did not commit. Check that user has CREATE ON SCHEMA public."
        )

    logger.info("platform_config schema ensured (public.platform_config)")


# ─── raw DB operations ────────────────────────────────────────────────────────

async def db_get(
    pool: asyncpg.Pool,
    tenant_id: str,
    namespace: str,
    key: str,
) -> Any | None:
    """Two-level lookup: tenant-specific wins over global. Returns parsed value or None."""
    row = await pool.fetchrow(
        """
        SELECT value FROM public.platform_config
        WHERE tenant_id IN ($1, $2)
          AND namespace = $3
          AND key       = $4
        ORDER BY CASE WHEN tenant_id = $1 THEN 0 ELSE 1 END
        LIMIT 1
        """,
        tenant_id, GLOBAL, namespace, key,
    )
    return json.loads(row["value"]) if row else None


async def db_get_raw(
    pool: asyncpg.Pool,
    tenant_id: str,
    namespace: str,
    key: str,
) -> Any | None:
    """Single-row lookup — exact (tenant_id, namespace, key), no fallback."""
    row = await pool.fetchrow(
        "SELECT value, description, updated_at FROM public.platform_config "
        "WHERE tenant_id = $1 AND namespace = $2 AND key = $3",
        tenant_id, namespace, key,
    )
    if not row:
        return None
    return {
        "tenant_id":   tenant_id,
        "namespace":   namespace,
        "key":         key,
        "value":       json.loads(row["value"]),
        "description": row["description"],
        "updated_at":  row["updated_at"].isoformat(),
    }


async def db_set(
    pool: asyncpg.Pool,
    tenant_id: str,
    namespace: str,
    key: str,
    value: Any,
    description: str = "",
) -> None:
    """Upsert a config entry. tenant_id='__global__' sets the platform default."""
    await pool.execute(
        """
        INSERT INTO public.platform_config (tenant_id, namespace, key, value, description, updated_at)
        VALUES ($1, $2, $3, $4::jsonb, $5, now())
        ON CONFLICT (tenant_id, namespace, key) DO UPDATE
            SET value       = EXCLUDED.value,
                description = EXCLUDED.description,
                updated_at  = now()
        """,
        tenant_id, namespace, key, json.dumps(value), description,
    )


async def db_delete(
    pool: asyncpg.Pool,
    tenant_id: str,
    namespace: str,
    key: str,
) -> bool:
    """Returns True if a row was deleted."""
    result = await pool.execute(
        "DELETE FROM public.platform_config WHERE tenant_id = $1 AND namespace = $2 AND key = $3",
        tenant_id, namespace, key,
    )
    return result.endswith("1")


async def db_list_namespace(
    pool: asyncpg.Pool,
    tenant_id: str,
    namespace: str,
) -> dict[str, Any]:
    """
    All resolved keys in a namespace for a given tenant.
    Tenant-specific values override globals.
    Returns {key: value}.
    """
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (key) key, value
        FROM public.platform_config
        WHERE tenant_id IN ($1, $2)
          AND namespace = $3
        ORDER BY key, CASE WHEN tenant_id = $1 THEN 0 ELSE 1 END
        """,
        tenant_id, GLOBAL, namespace,
    )
    return {r["key"]: json.loads(r["value"]) for r in rows}


async def db_list_all(
    pool: asyncpg.Pool,
    tenant_id: str,
) -> dict[str, dict[str, Any]]:
    """
    All resolved config for a tenant, grouped by namespace.
    Returns {namespace: {key: value}}.
    """
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (namespace, key) namespace, key, value
        FROM public.platform_config
        WHERE tenant_id IN ($1, $2)
        ORDER BY namespace, key, CASE WHEN tenant_id = $1 THEN 0 ELSE 1 END
        """,
        tenant_id, GLOBAL,
    )
    result: dict[str, dict] = {}
    for r in rows:
        ns = r["namespace"]
        if ns not in result:
            result[ns] = {}
        result[ns][r["key"]] = json.loads(r["value"])
    return result


async def db_key_provenance(
    pool: asyncpg.Pool,
    tenant_id: str,
    namespace: str,
) -> dict[str, dict[str, Any]]:
    """Para cada key do namespace: o que existe no GLOBAL e o que existe no tenant.

    Uma consulta só, os dois escopos juntos — não duas. Duas consultas dariam duas
    fotos de instantes diferentes, e a pergunta aqui é justamente sobre a RELAÇÃO
    entre elas: um `set` no meio do caminho faria o relatório dizer que o tenant não
    sombreia uma key que ele acabou de sombrear.

    Devolve `{key: {"global": <valor>, "tenant": <valor>}}` com o lado **AUSENTE
    DA CHAVE** quando não há linha — nunca `None`. A distinção é real: um `null`
    gravado no tenant VENCE o global; a ausência de linha não vence nada. Se a
    ausência virasse `None`, os dois casos ficariam indistinguíveis e o relatório
    diria "o tenant sobrescreve" para quem não sobrescreve.
    """
    rows = await pool.fetch(
        "SELECT key, tenant_id, value FROM public.platform_config "
        "WHERE namespace = $1 AND tenant_id IN ($2, $3)",
        namespace, tenant_id, GLOBAL,
    )
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        lado = "global" if r["tenant_id"] == GLOBAL else "tenant"
        out.setdefault(r["key"], {})[lado] = json.loads(r["value"])
    return out


async def db_tenants_overriding(
    pool: asyncpg.Pool,
    namespace: str,
    key: str,
) -> list[str]:
    """Tenants com linha PRÓPRIA para (namespace, key) — ou seja, para quem uma
    escrita no `__global__` não tem efeito nenhum.

    A resolução é `LIMIT 1` com o tenant na frente: o override substitui o global
    POR INTEIRO. Então esta lista não é curiosidade, é a resposta a *"a escrita que
    acabei de fazer alcança quem?"*.
    """
    rows = await pool.fetch(
        "SELECT tenant_id FROM public.platform_config "
        "WHERE namespace = $1 AND key = $2 AND tenant_id <> $3 ORDER BY tenant_id",
        namespace, key, GLOBAL,
    )
    return [r["tenant_id"] for r in rows]


async def db_list_namespace_entries(
    pool: asyncpg.Pool,
    tenant_id: str,
    namespace: str,
) -> list[dict]:
    """
    Raw (non-resolved) entries for a given (tenant_id, namespace).
    Used by the admin list endpoint to show what is explicitly set.
    """
    rows = await pool.fetch(
        "SELECT key, value, description, updated_at FROM public.platform_config "
        "WHERE tenant_id = $1 AND namespace = $2 ORDER BY key",
        tenant_id, namespace,
    )
    return [
        {
            "key":         r["key"],
            "value":       json.loads(r["value"]),
            "description": r["description"],
            "updated_at":  r["updated_at"].isoformat(),
        }
        for r in rows
    ]
