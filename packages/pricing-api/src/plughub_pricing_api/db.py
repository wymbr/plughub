"""
db.py
asyncpg DDL + CRUD for the pricing module.

Schema: pricing (dedicated PostgreSQL schema)

Tables:
  pricing.installation_resources
    — configured resource slots per tenant/installation
    — pool_type: 'base' (always billed) | 'reserve' (billed when active)

  pricing.reserve_activation_log
    — full-day billing: if a reserve pool is activated any time on a given date,
      that entire calendar day is billable.
    — deactivation_date IS NULL → pool is currently active.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger("plughub.pricing.db")

# ─── DDL ──────────────────────────────────────────────────────────────────────

_DDL = """
CREATE SCHEMA IF NOT EXISTS pricing;

CREATE TABLE IF NOT EXISTS pricing.installation_resources (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT        NOT NULL,
    installation_id TEXT        NOT NULL DEFAULT 'default',
    resource_type   TEXT        NOT NULL,
    quantity        INTEGER     NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    pool_type       TEXT        NOT NULL DEFAULT 'base' CHECK (pool_type IN ('base', 'reserve')),
    reserve_pool_id TEXT,
    active          BOOLEAN     NOT NULL DEFAULT TRUE,
    billing_unit    TEXT        NOT NULL DEFAULT 'monthly' CHECK (billing_unit IN ('monthly', 'daily')),
    label           TEXT        NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_installation_resource
        UNIQUE (tenant_id, installation_id, resource_type, reserve_pool_id)
);

CREATE INDEX IF NOT EXISTS idx_pricing_resources_tenant
    ON pricing.installation_resources (tenant_id, installation_id);

CREATE TABLE IF NOT EXISTS pricing.reserve_activation_log (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         TEXT        NOT NULL,
    reserve_pool_id   TEXT        NOT NULL,
    activation_date   DATE        NOT NULL,
    deactivation_date DATE,
    activated_by      TEXT        NOT NULL DEFAULT 'operator',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_reserve_activation
        UNIQUE (tenant_id, reserve_pool_id, activation_date)
);

CREATE INDEX IF NOT EXISTS idx_reserve_log_pool
    ON pricing.reserve_activation_log (tenant_id, reserve_pool_id, activation_date);
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DDL)
    logger.info("pricing schema ensured")


# ─── installation_resources ───────────────────────────────────────────────────

async def list_resources(
    pool: asyncpg.Pool,
    tenant_id: str,
    installation_id: str = "default",
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, tenant_id, installation_id, resource_type, quantity,
               pool_type, reserve_pool_id, active, billing_unit, label,
               created_at, updated_at
        FROM pricing.installation_resources
        WHERE tenant_id = $1 AND installation_id = $2
        ORDER BY pool_type, reserve_pool_id NULLS FIRST, resource_type
        """,
        tenant_id, installation_id,
    )
    return [_row_to_dict(r) for r in rows]


async def get_resource(pool: asyncpg.Pool, resource_id: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM pricing.installation_resources WHERE id = $1",
        resource_id,
    )
    return _row_to_dict(row) if row else None


async def upsert_resource(
    pool: asyncpg.Pool,
    tenant_id: str,
    installation_id: str,
    resource_type: str,
    quantity: int,
    pool_type: str = "base",
    reserve_pool_id: str | None = None,
    billing_unit: str = "monthly",
    label: str = "",
) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO pricing.installation_resources
            (tenant_id, installation_id, resource_type, quantity,
             pool_type, reserve_pool_id, billing_unit, label, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
        ON CONFLICT (tenant_id, installation_id, resource_type, reserve_pool_id) DO UPDATE
            SET quantity     = EXCLUDED.quantity,
                pool_type    = EXCLUDED.pool_type,
                billing_unit = EXCLUDED.billing_unit,
                label        = EXCLUDED.label,
                updated_at   = now()
        RETURNING *
        """,
        tenant_id, installation_id, resource_type, quantity,
        pool_type, reserve_pool_id, billing_unit, label,
    )
    return _row_to_dict(row)


async def delete_resource(pool: asyncpg.Pool, resource_id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM pricing.installation_resources WHERE id = $1",
        resource_id,
    )
    return result.endswith("1")


async def set_reserve_active(
    pool: asyncpg.Pool,
    tenant_id: str,
    reserve_pool_id: str,
    active: bool,
) -> int:
    """Toggles `active` for all resources belonging to a reserve pool.
    Returns number of rows updated."""
    result = await pool.execute(
        """
        UPDATE pricing.installation_resources
        SET active = $1, updated_at = now()
        WHERE tenant_id = $2 AND reserve_pool_id = $3 AND pool_type = 'reserve'
        """,
        active, tenant_id, reserve_pool_id,
    )
    # result = "UPDATE N"
    parts = result.split()
    return int(parts[1]) if len(parts) == 2 else 0


# ─── reserve_activation_log ───────────────────────────────────────────────────

async def record_activation(
    pool: asyncpg.Pool,
    tenant_id: str,
    reserve_pool_id: str,
    activated_by: str = "operator",
) -> dict:
    """Records an activation on today's date. Idempotent — skips if already active today."""
    today = date.today()
    row = await pool.fetchrow(
        """
        INSERT INTO pricing.reserve_activation_log
            (tenant_id, reserve_pool_id, activation_date, activated_by)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (tenant_id, reserve_pool_id, activation_date) DO UPDATE
            SET deactivation_date = NULL,
                activated_by      = EXCLUDED.activated_by
        RETURNING *
        """,
        tenant_id, reserve_pool_id, today, activated_by,
    )
    return _log_row_to_dict(row)


async def record_deactivation(
    pool: asyncpg.Pool,
    tenant_id: str,
    reserve_pool_id: str,
) -> bool:
    """Closes open activation records (deactivation_date = today).
    Returns True if any rows were updated."""
    today = date.today()
    result = await pool.execute(
        """
        UPDATE pricing.reserve_activation_log
        SET deactivation_date = $1
        WHERE tenant_id = $2 AND reserve_pool_id = $3
          AND deactivation_date IS NULL
        """,
        today, tenant_id, reserve_pool_id,
    )
    parts = result.split()
    return int(parts[1]) if len(parts) == 2 else 0 > 0


async def count_active_days(
    pool: asyncpg.Pool,
    tenant_id: str,
    reserve_pool_id: str,
    cycle_start: date,
    cycle_end: date,
) -> int:
    """
    Counts distinct billable calendar days for a reserve pool within a billing cycle.
    A day is billable if the pool was active at any point during that day.
    Full-day billing: activation on day D → D is fully billable.
    """
    rows = await pool.fetch(
        """
        SELECT activation_date,
               COALESCE(deactivation_date, $4::date) AS end_date
        FROM pricing.reserve_activation_log
        WHERE tenant_id = $1
          AND reserve_pool_id = $2
          AND activation_date <= $4
          AND (deactivation_date IS NULL OR deactivation_date >= $3)
        """,
        tenant_id, reserve_pool_id, cycle_start, cycle_end,
    )

    billable: set[date] = set()
    for r in rows:
        start = max(r["activation_date"], cycle_start)
        end   = min(r["end_date"],        cycle_end)
        current = start
        while current <= end:
            billable.add(current)
            current = date.fromordinal(current.toordinal() + 1)

    return len(billable)


async def list_activation_log(
    pool: asyncpg.Pool,
    tenant_id: str,
    reserve_pool_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    if reserve_pool_id:
        rows = await pool.fetch(
            """
            SELECT * FROM pricing.reserve_activation_log
            WHERE tenant_id = $1 AND reserve_pool_id = $2
            ORDER BY activation_date DESC
            LIMIT $3
            """,
            tenant_id, reserve_pool_id, limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM pricing.reserve_activation_log
            WHERE tenant_id = $1
            ORDER BY activation_date DESC
            LIMIT $2
            """,
            tenant_id, limit,
        )
    return [_log_row_to_dict(r) for r in rows]


# ─── helpers ──────────────────────────────────────────────────────────────────

def _row_to_dict(r: asyncpg.Record) -> dict:
    return {
        "id":              str(r["id"]),
        "tenant_id":       r["tenant_id"],
        "installation_id": r["installation_id"],
        "resource_type":   r["resource_type"],
        "quantity":        r["quantity"],
        "pool_type":       r["pool_type"],
        "reserve_pool_id": r["reserve_pool_id"],
        "active":          r["active"],
        "billing_unit":    r["billing_unit"],
        "label":           r["label"],
        "created_at":      r["created_at"].isoformat(),
        "updated_at":      r["updated_at"].isoformat(),
    }


def _log_row_to_dict(r: asyncpg.Record) -> dict:
    return {
        "id":                str(r["id"]),
        "tenant_id":         r["tenant_id"],
        "reserve_pool_id":   r["reserve_pool_id"],
        "activation_date":   r["activation_date"].isoformat(),
        "deactivation_date": r["deactivation_date"].isoformat() if r["deactivation_date"] else None,
        "activated_by":      r["activated_by"],
        "created_at":        r["created_at"].isoformat(),
    }
