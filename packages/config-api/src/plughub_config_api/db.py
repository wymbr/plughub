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

_DDL = """
CREATE TABLE IF NOT EXISTS platform_config (
    id          SERIAL PRIMARY KEY,
    tenant_id   TEXT        NOT NULL DEFAULT '__global__',
    namespace   TEXT        NOT NULL,
    key         TEXT        NOT NULL,
    value       JSONB       NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_platform_config UNIQUE (tenant_id, namespace, key)
);

CREATE INDEX IF NOT EXISTS idx_platform_config_lookup
    ON platform_config (tenant_id, namespace, key);
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DDL)
    logger.info("platform_config schema ensured")


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
        SELECT value FROM platform_config
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
        "SELECT value, description, updated_at FROM platform_config "
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
        INSERT INTO platform_config (tenant_id, namespace, key, value, description, updated_at)
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
        "DELETE FROM platform_config WHERE tenant_id = $1 AND namespace = $2 AND key = $3",
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
        FROM platform_config
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
        FROM platform_config
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
        "SELECT key, value, description, updated_at FROM platform_config "
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
