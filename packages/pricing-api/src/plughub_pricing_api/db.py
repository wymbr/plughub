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
    -- NULLS NOT DISTINCT (PG15+): reserve_pool_id é NULL nos recursos base; sem
    -- isso, NULL ≠ NULL e o ON CONFLICT do upsert nunca dispara → cada POST
    -- insere linha duplicada (bug descoberto 2026-06-04 no quota sync).
    CONSTRAINT uq_installation_resource
        UNIQUE NULLS NOT DISTINCT (tenant_id, installation_id, resource_type, reserve_pool_id)
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
        async with conn.transaction():
            await conn.execute(_DDL)
            await _migrate_uq_nulls_not_distinct(conn)
    logger.info("pricing schema ensured")


async def _migrate_uq_nulls_not_distinct(conn: asyncpg.Connection) -> None:
    """
    Migração (2026-06-04): bases criadas antes do NULLS NOT DISTINCT têm a
    constraint antiga (NULL ≠ NULL → upsert duplicava recursos base). Detecta
    pela pg_index, deduplica (mantém a linha mais recente por chave lógica —
    a última escrita expressa a intenção atual do operador) e recria a constraint.
    """
    row = await conn.fetchrow(
        """
        SELECT i.indnullsnotdistinct AS nnd
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'pricing' AND c.relname = 'uq_installation_resource'
        """
    )
    if row is None or row["nnd"]:
        return  # constraint não existe (tabela nova já correta) ou já migrada

    deduped = await conn.execute(
        """
        DELETE FROM pricing.installation_resources a
        USING pricing.installation_resources b
        WHERE a.tenant_id = b.tenant_id
          AND a.installation_id = b.installation_id
          AND a.resource_type = b.resource_type
          AND a.reserve_pool_id IS NOT DISTINCT FROM b.reserve_pool_id
          AND a.id <> b.id
          AND (a.updated_at < b.updated_at
               OR (a.updated_at = b.updated_at AND a.id < b.id))
        """
    )
    await conn.execute(
        "ALTER TABLE pricing.installation_resources DROP CONSTRAINT uq_installation_resource"
    )
    await conn.execute(
        """
        ALTER TABLE pricing.installation_resources
        ADD CONSTRAINT uq_installation_resource
        UNIQUE NULLS NOT DISTINCT (tenant_id, installation_id, resource_type, reserve_pool_id)
        """
    )
    logger.info("migrated uq_installation_resource to NULLS NOT DISTINCT (%s)", deduped)


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


# ─── configured capacity (Fase 2 — Pools/Infra report) ───────────────────────
#
# Capacidade configurada (contratada) por tipo de recurso. Consumida pelo
# analytics-api como denominador do TOTAL no /reports/pools/occupancy
# (decisão 2026-06-04: per-pool continua provisionada; só o total usa pricing).
# base = pool_type 'base' (sempre ativa); reserve_active = reservas ativas hoje.

_AGENT_CAPACITY_TYPES = ("ai_agent", "human_agent")


async def get_capacity(
    pool: asyncpg.Pool,
    tenant_id: str,
    installation_id: str | None = "default",
) -> dict:
    """installation_id=None agrega todas as instalações do tenant (quota é por tenant)."""
    where_inst = "AND installation_id = $2" if installation_id is not None else ""
    args = [tenant_id] + ([installation_id] if installation_id is not None else [])
    rows = await pool.fetch(
        f"""
        SELECT resource_type,
               SUM(quantity) FILTER (WHERE pool_type = 'base')                 AS base,
               SUM(quantity) FILTER (WHERE pool_type = 'reserve' AND active)   AS reserve_active
        FROM pricing.installation_resources
        WHERE tenant_id = $1 {where_inst} AND active
        GROUP BY resource_type
        ORDER BY resource_type
        """,
        *args,
    )
    by_type = [
        {
            "resource_type":  r["resource_type"],
            "base":           int(r["base"] or 0),
            "reserve_active": int(r["reserve_active"] or 0),
            "total":          int(r["base"] or 0) + int(r["reserve_active"] or 0),
        }
        for r in rows
    ]
    agent_total = sum(t["total"] for t in by_type if t["resource_type"] in _AGENT_CAPACITY_TYPES)
    return {
        "tenant_id":            tenant_id,
        "installation_id":      installation_id,
        "by_type":              by_type,
        "agent_capacity_total": agent_total,
    }


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
