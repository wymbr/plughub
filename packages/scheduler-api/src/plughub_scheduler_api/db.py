"""
db.py
DDL and raw asyncpg operations for the scheduler-api (Camada 2 — fonte de verdade).

Tables (schema 'scheduler'):
  scheduler.agendas           — the schedule descriptor + runtime (next/last fire, status)
  scheduler.agenda_dispatches — the dispatch ledger (one row per occurrence)

`validity` and `schedule` are stored as JSONB blobs whose shape is the Zod contract
in @plughub/schemas/scheduler.ts (validated on ingest by the Pydantic models in router.py).
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger("plughub.scheduler.db")

_DDL_SCHEMA = "CREATE SCHEMA IF NOT EXISTS scheduler"

_DDL_AGENDAS = """
CREATE TABLE IF NOT EXISTS scheduler.agendas (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT        NOT NULL,
    name            TEXT        NOT NULL,
    target_pool_id  TEXT        NOT NULL,
    payload         JSONB       NOT NULL DEFAULT '{}',
    timezone        TEXT        NOT NULL DEFAULT 'America/Sao_Paulo',
    calendar_id     TEXT,
    status          TEXT        NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active','paused','completed','expired','cancelled')),
    validity        JSONB       NOT NULL,
    schedule        JSONB       NOT NULL,
    misfire_policy  TEXT        NOT NULL DEFAULT 'skip'
                                CHECK (misfire_policy IN ('fire_late','skip','fire_all_missed')),
    next_fire_at    TIMESTAMPTZ,
    last_fired_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_AGENDAS_IDX_TENANT = (
    "CREATE INDEX IF NOT EXISTS idx_agendas_tenant "
    "ON scheduler.agendas (tenant_id, created_at DESC)"
)

# Poller / re-hydration: active agendas with a computed next_fire_at.
_DDL_AGENDAS_IDX_DUE = (
    "CREATE INDEX IF NOT EXISTS idx_agendas_due "
    "ON scheduler.agendas (status, next_fire_at) WHERE status = 'active'"
)

_DDL_DISPATCHES = """
CREATE TABLE IF NOT EXISTS scheduler.agenda_dispatches (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    agenda_id        UUID        NOT NULL REFERENCES scheduler.agendas(id) ON DELETE CASCADE,
    tenant_id        TEXT        NOT NULL,
    scheduled_for    TIMESTAMPTZ NOT NULL,
    fired_at         TIMESTAMPTZ,
    result           TEXT        NOT NULL
                                 CHECK (result IN ('dispatched','failed','skipped')),
    session_id       TEXT,
    root_session_id  TEXT,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_DISPATCHES_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_dispatches_agenda "
    "ON scheduler.agenda_dispatches (agenda_id, created_at DESC)"
)


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        # Explicit transaction: commit before the connection returns to the pool.
        async with conn.transaction():
            await conn.execute(_DDL_SCHEMA)
            await conn.execute(_DDL_AGENDAS)
            await conn.execute(_DDL_AGENDAS_IDX_TENANT)
            await conn.execute(_DDL_AGENDAS_IDX_DUE)
            await conn.execute(_DDL_DISPATCHES)
            await conn.execute(_DDL_DISPATCHES_IDX)
    logger.info("scheduler schema ensured")


# ── Row mappers ───────────────────────────────────────────────────────────────

def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


def _row_to_agenda(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":             str(row["id"]),
        "tenant_id":      row["tenant_id"],
        "name":           row["name"],
        "target_pool_id": row["target_pool_id"],
        "payload":        json.loads(row["payload"]),
        "timezone":       row["timezone"],
        "calendar_id":    row["calendar_id"],
        "status":         row["status"],
        "validity":       json.loads(row["validity"]),
        "schedule":       json.loads(row["schedule"]),
        "misfire_policy": row["misfire_policy"],
        "next_fire_at":   _iso(row["next_fire_at"]),
        "last_fired_at":  _iso(row["last_fired_at"]),
        "created_at":     _iso(row["created_at"]),
        "updated_at":     _iso(row["updated_at"]),
    }


def _row_to_dispatch(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":              str(row["id"]),
        "agenda_id":       str(row["agenda_id"]),
        "tenant_id":       row["tenant_id"],
        "scheduled_for":   _iso(row["scheduled_for"]),
        "fired_at":        _iso(row["fired_at"]),
        "result":          row["result"],
        "session_id":      row["session_id"],
        "root_session_id": row["root_session_id"],
        "error":           row["error"],
        "created_at":      _iso(row["created_at"]),
    }


# ── Agendas — CRUD ────────────────────────────────────────────────────────────

async def db_list_agendas(
    pool: asyncpg.Pool,
    tenant_id: str,
    status: str | None = None,
) -> list[dict]:
    if status:
        rows = await pool.fetch(
            "SELECT * FROM scheduler.agendas WHERE tenant_id = $1 AND status = $2 "
            "ORDER BY created_at DESC",
            tenant_id, status,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM scheduler.agendas WHERE tenant_id = $1 ORDER BY created_at DESC",
            tenant_id,
        )
    return [_row_to_agenda(r) for r in rows]


async def db_get_agenda(pool: asyncpg.Pool, tenant_id: str, id: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM scheduler.agendas WHERE id = $1 AND tenant_id = $2",
        UUID(id), tenant_id,
    )
    return _row_to_agenda(row) if row else None


async def db_create_agenda(pool: asyncpg.Pool, tenant_id: str, data: dict) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO scheduler.agendas
            (tenant_id, name, target_pool_id, payload, timezone, calendar_id,
             status, validity, schedule, misfire_policy, next_fire_at)
        VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7,$8::jsonb,$9::jsonb,$10,$11)
        RETURNING *
        """,
        tenant_id,
        data["name"], data["target_pool_id"],
        json.dumps(data.get("payload", {})),
        data.get("timezone", "America/Sao_Paulo"),
        data.get("calendar_id"),
        data.get("status", "active"),
        json.dumps(data["validity"]),
        json.dumps(data["schedule"]),
        data.get("misfire_policy", "skip"),
        data.get("next_fire_at"),
    )
    return _row_to_agenda(row)


async def db_update_agenda(pool: asyncpg.Pool, tenant_id: str, id: str, data: dict) -> dict | None:
    row = await pool.fetchrow(
        """
        UPDATE scheduler.agendas
        SET name           = COALESCE($3, name),
            target_pool_id = COALESCE($4, target_pool_id),
            payload        = COALESCE($5::jsonb, payload),
            timezone       = COALESCE($6, timezone),
            calendar_id    = COALESCE($7, calendar_id),
            status         = COALESCE($8, status),
            validity       = COALESCE($9::jsonb, validity),
            schedule       = COALESCE($10::jsonb, schedule),
            misfire_policy = COALESCE($11, misfire_policy),
            updated_at     = now()
        WHERE id = $1 AND tenant_id = $2
        RETURNING *
        """,
        UUID(id), tenant_id,
        data.get("name"), data.get("target_pool_id"),
        json.dumps(data["payload"]) if "payload" in data else None,
        data.get("timezone"), data.get("calendar_id"), data.get("status"),
        json.dumps(data["validity"]) if "validity" in data else None,
        json.dumps(data["schedule"]) if "schedule" in data else None,
        data.get("misfire_policy"),
    )
    return _row_to_agenda(row) if row else None


async def db_set_agenda_status(
    pool: asyncpg.Pool, tenant_id: str, id: str, status: str,
) -> dict | None:
    row = await pool.fetchrow(
        "UPDATE scheduler.agendas SET status = $3, updated_at = now() "
        "WHERE id = $1 AND tenant_id = $2 RETURNING *",
        UUID(id), tenant_id, status,
    )
    return _row_to_agenda(row) if row else None


async def db_delete_agenda(pool: asyncpg.Pool, tenant_id: str, id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM scheduler.agendas WHERE id = $1 AND tenant_id = $2",
        UUID(id), tenant_id,
    )
    return result.endswith("1")


# ── Runtime updates (used by the poller — Camada 1) ───────────────────────────

async def db_update_agenda_runtime(
    pool: asyncpg.Pool,
    id: str,
    *,
    next_fire_at: Any = None,
    last_fired_at: Any = None,
    status: str | None = None,
) -> None:
    """Update poller-owned fields. Only writes non-None args (next_fire_at cleared
    explicitly by passing the sentinel handled at the call site)."""
    await pool.execute(
        """
        UPDATE scheduler.agendas
        SET next_fire_at  = COALESCE($2, next_fire_at),
            last_fired_at = COALESCE($3, last_fired_at),
            status        = COALESCE($4, status),
            updated_at    = now()
        WHERE id = $1
        """,
        UUID(id), next_fire_at, last_fired_at, status,
    )


async def db_set_next_fire_at(pool: asyncpg.Pool, id: str, next_fire_at: Any) -> None:
    """Set (or clear, when None) the next_fire_at — needed because COALESCE cannot
    clear a column back to NULL."""
    await pool.execute(
        "UPDATE scheduler.agendas SET next_fire_at = $2, updated_at = now() WHERE id = $1",
        UUID(id), next_fire_at,
    )


async def db_list_active_agendas(pool: asyncpg.Pool) -> list[dict]:
    """All active agendas across tenants — for re-hydration of Camada 1 on boot."""
    rows = await pool.fetch(
        "SELECT * FROM scheduler.agendas WHERE status = 'active'"
    )
    return [_row_to_agenda(r) for r in rows]


# ── Dispatch ledger ───────────────────────────────────────────────────────────

async def db_insert_dispatch(pool: asyncpg.Pool, tenant_id: str, data: dict) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO scheduler.agenda_dispatches
            (agenda_id, tenant_id, scheduled_for, fired_at, result,
             session_id, root_session_id, error)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        RETURNING *
        """,
        UUID(data["agenda_id"]), tenant_id,
        data["scheduled_for"], data.get("fired_at"),
        data["result"], data.get("session_id"),
        data.get("root_session_id"), data.get("error"),
    )
    return _row_to_dispatch(row)


async def db_list_dispatches(
    pool: asyncpg.Pool, tenant_id: str, agenda_id: str, limit: int = 100,
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT * FROM scheduler.agenda_dispatches
        WHERE agenda_id = $1 AND tenant_id = $2
        ORDER BY created_at DESC
        LIMIT $3
        """,
        UUID(agenda_id), tenant_id, limit,
    )
    return [_row_to_dispatch(r) for r in rows]
