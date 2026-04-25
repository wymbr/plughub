"""
db.py
DDL and raw asyncpg operations for the calendar-api.

Tables (all in schema 'calendar' to avoid conflicts with other services):
  calendar.holiday_sets       — named sets of holiday dates
  calendar.calendars          — calendar definitions with weekly schedule
  calendar.calendar_associations — entity ↔ calendar bindings with operator/priority
"""
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger("plughub.calendar.db")

_DDL = """
CREATE SCHEMA IF NOT EXISTS calendar;

CREATE TABLE IF NOT EXISTS calendar.holiday_sets (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    installation_id  TEXT        NOT NULL,
    organization_id  TEXT        NOT NULL,
    tenant_id        TEXT,                           -- NULL = org-level
    scope            TEXT        NOT NULL DEFAULT 'tenant'
                                 CHECK (scope IN ('installation','organization','tenant')),
    name             TEXT        NOT NULL,
    description      TEXT        NOT NULL DEFAULT '',
    year             INTEGER,
    holidays         JSONB       NOT NULL DEFAULT '[]',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_holiday_set UNIQUE (organization_id, COALESCE(tenant_id,''), name)
);

CREATE TABLE IF NOT EXISTS calendar.calendars (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    installation_id  TEXT        NOT NULL,
    organization_id  TEXT        NOT NULL,
    tenant_id        TEXT,                           -- NULL = org-level
    scope            TEXT        NOT NULL DEFAULT 'tenant'
                                 CHECK (scope IN ('installation','organization','tenant')),
    name             TEXT        NOT NULL,
    description      TEXT        NOT NULL DEFAULT '',
    timezone         TEXT        NOT NULL DEFAULT 'America/Sao_Paulo',
    weekly_schedule  JSONB       NOT NULL DEFAULT '[]',
    holiday_set_ids  JSONB       NOT NULL DEFAULT '[]',
    exceptions       JSONB       NOT NULL DEFAULT '[]',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_calendar UNIQUE (organization_id, COALESCE(tenant_id,''), name)
);

CREATE INDEX IF NOT EXISTS idx_calendars_org_tenant
    ON calendar.calendars (organization_id, tenant_id);

CREATE TABLE IF NOT EXISTS calendar.calendar_associations (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT        NOT NULL,
    entity_type  TEXT        NOT NULL
                             CHECK (entity_type IN ('tenant','channel','pool','workflow')),
    entity_id    TEXT        NOT NULL,
    calendar_id  UUID        NOT NULL REFERENCES calendar.calendars(id) ON DELETE CASCADE,
    operator     TEXT        NOT NULL DEFAULT 'UNION'
                             CHECK (operator IN ('UNION','INTERSECTION')),
    priority     INTEGER     NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_calendar_assoc UNIQUE (tenant_id, entity_type, entity_id, calendar_id)
);

CREATE INDEX IF NOT EXISTS idx_assoc_entity
    ON calendar.calendar_associations (tenant_id, entity_type, entity_id, priority);
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DDL)
    logger.info("calendar schema ensured")


# ── Holiday Sets ──────────────────────────────────────────────────────────────

def _row_to_holiday_set(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":              str(row["id"]),
        "installation_id": row["installation_id"],
        "organization_id": row["organization_id"],
        "tenant_id":       row["tenant_id"],
        "scope":           row["scope"],
        "name":            row["name"],
        "description":     row["description"],
        "year":            row["year"],
        "holidays":        json.loads(row["holidays"]),
        "created_at":      row["created_at"].isoformat(),
        "updated_at":      row["updated_at"].isoformat(),
    }


async def db_list_holiday_sets(
    pool: asyncpg.Pool,
    organization_id: str,
    tenant_id: str | None = None,
) -> list[dict]:
    """List holiday sets visible to the given org/tenant (own + org-level)."""
    rows = await pool.fetch(
        """
        SELECT * FROM calendar.holiday_sets
        WHERE organization_id = $1
          AND (tenant_id = $2 OR tenant_id IS NULL)
        ORDER BY name
        """,
        organization_id, tenant_id,
    )
    return [_row_to_holiday_set(r) for r in rows]


async def db_get_holiday_set(pool: asyncpg.Pool, id: str) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM calendar.holiday_sets WHERE id = $1", UUID(id))
    return _row_to_holiday_set(row) if row else None


async def db_create_holiday_set(pool: asyncpg.Pool, data: dict) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO calendar.holiday_sets
            (installation_id, organization_id, tenant_id, scope, name, description,
             year, holidays)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
        RETURNING *
        """,
        data["installation_id"], data["organization_id"], data.get("tenant_id"),
        data.get("scope", "tenant"), data["name"], data.get("description", ""),
        data.get("year"), json.dumps(data.get("holidays", [])),
    )
    return _row_to_holiday_set(row)


async def db_update_holiday_set(pool: asyncpg.Pool, id: str, data: dict) -> dict | None:
    row = await pool.fetchrow(
        """
        UPDATE calendar.holiday_sets
        SET name        = COALESCE($2, name),
            description = COALESCE($3, description),
            year        = COALESCE($4, year),
            holidays    = COALESCE($5::jsonb, holidays),
            updated_at  = now()
        WHERE id = $1
        RETURNING *
        """,
        UUID(id), data.get("name"), data.get("description"),
        data.get("year"),
        json.dumps(data["holidays"]) if "holidays" in data else None,
    )
    return _row_to_holiday_set(row) if row else None


async def db_delete_holiday_set(pool: asyncpg.Pool, id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM calendar.holiday_sets WHERE id = $1", UUID(id)
    )
    return result.endswith("1")


# ── Calendars ─────────────────────────────────────────────────────────────────

def _row_to_calendar(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":              str(row["id"]),
        "installation_id": row["installation_id"],
        "organization_id": row["organization_id"],
        "tenant_id":       row["tenant_id"],
        "scope":           row["scope"],
        "name":            row["name"],
        "description":     row["description"],
        "timezone":        row["timezone"],
        "weekly_schedule": json.loads(row["weekly_schedule"]),
        "holiday_set_ids": json.loads(row["holiday_set_ids"]),
        "exceptions":      json.loads(row["exceptions"]),
        "created_at":      row["created_at"].isoformat(),
        "updated_at":      row["updated_at"].isoformat(),
    }


async def db_list_calendars(
    pool: asyncpg.Pool,
    organization_id: str,
    tenant_id: str | None = None,
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT * FROM calendar.calendars
        WHERE organization_id = $1
          AND (tenant_id = $2 OR tenant_id IS NULL)
        ORDER BY name
        """,
        organization_id, tenant_id,
    )
    return [_row_to_calendar(r) for r in rows]


async def db_get_calendar(pool: asyncpg.Pool, id: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM calendar.calendars WHERE id = $1", UUID(id)
    )
    return _row_to_calendar(row) if row else None


async def db_create_calendar(pool: asyncpg.Pool, data: dict) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO calendar.calendars
            (installation_id, organization_id, tenant_id, scope, name, description,
             timezone, weekly_schedule, holiday_set_ids, exceptions)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb)
        RETURNING *
        """,
        data["installation_id"], data["organization_id"], data.get("tenant_id"),
        data.get("scope", "tenant"), data["name"], data.get("description", ""),
        data.get("timezone", "America/Sao_Paulo"),
        json.dumps(data.get("weekly_schedule", [])),
        json.dumps(data.get("holiday_set_ids", [])),
        json.dumps(data.get("exceptions", [])),
    )
    return _row_to_calendar(row)


async def db_update_calendar(pool: asyncpg.Pool, id: str, data: dict) -> dict | None:
    row = await pool.fetchrow(
        """
        UPDATE calendar.calendars
        SET name            = COALESCE($2, name),
            description     = COALESCE($3, description),
            timezone        = COALESCE($4, timezone),
            weekly_schedule = COALESCE($5::jsonb, weekly_schedule),
            holiday_set_ids = COALESCE($6::jsonb, holiday_set_ids),
            exceptions      = COALESCE($7::jsonb, exceptions),
            updated_at      = now()
        WHERE id = $1
        RETURNING *
        """,
        UUID(id),
        data.get("name"), data.get("description"), data.get("timezone"),
        json.dumps(data["weekly_schedule"]) if "weekly_schedule" in data else None,
        json.dumps(data["holiday_set_ids"]) if "holiday_set_ids" in data else None,
        json.dumps(data["exceptions"]) if "exceptions" in data else None,
    )
    return _row_to_calendar(row) if row else None


async def db_delete_calendar(pool: asyncpg.Pool, id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM calendar.calendars WHERE id = $1", UUID(id)
    )
    return result.endswith("1")


# ── Calendar Associations ─────────────────────────────────────────────────────

def _row_to_assoc(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":          str(row["id"]),
        "tenant_id":   row["tenant_id"],
        "entity_type": row["entity_type"],
        "entity_id":   row["entity_id"],
        "calendar_id": str(row["calendar_id"]),
        "operator":    row["operator"],
        "priority":    row["priority"],
        "created_at":  row["created_at"].isoformat(),
    }


async def db_list_associations(
    pool: asyncpg.Pool,
    tenant_id: str,
    entity_type: str,
    entity_id: str,
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT * FROM calendar.calendar_associations
        WHERE tenant_id = $1 AND entity_type = $2 AND entity_id = $3
        ORDER BY priority, created_at
        """,
        tenant_id, entity_type, entity_id,
    )
    return [_row_to_assoc(r) for r in rows]


async def db_create_association(pool: asyncpg.Pool, data: dict) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO calendar.calendar_associations
            (tenant_id, entity_type, entity_id, calendar_id, operator, priority)
        VALUES ($1,$2,$3,$4,$5,$6)
        RETURNING *
        """,
        data["tenant_id"], data["entity_type"], data["entity_id"],
        UUID(data["calendar_id"]), data.get("operator", "UNION"),
        data.get("priority", 1),
    )
    return _row_to_assoc(row)


async def db_delete_association(pool: asyncpg.Pool, id: str) -> bool:
    result = await pool.execute(
        "DELETE FROM calendar.calendar_associations WHERE id = $1", UUID(id)
    )
    return result.endswith("1")


async def db_get_associations_for_engine(
    pool: asyncpg.Pool,
    tenant_id: str,
    entity_type: str,
    entity_id: str,
) -> list[dict]:
    """Returns associations joined with calendar data — used by the engine."""
    rows = await pool.fetch(
        """
        SELECT a.id, a.operator, a.priority,
               c.id AS calendar_id, c.timezone,
               c.weekly_schedule, c.holiday_set_ids, c.exceptions
        FROM calendar.calendar_associations a
        JOIN calendar.calendars c ON c.id = a.calendar_id
        WHERE a.tenant_id = $1 AND a.entity_type = $2 AND a.entity_id = $3
        ORDER BY a.priority, a.created_at
        """,
        tenant_id, entity_type, entity_id,
    )
    result = []
    for r in rows:
        hs_ids = json.loads(r["holiday_set_ids"])
        result.append({
            "assoc_id":       str(r["id"]),
            "operator":       r["operator"],
            "priority":       r["priority"],
            "calendar_id":    str(r["calendar_id"]),
            "timezone":       r["timezone"],
            "weekly_schedule": json.loads(r["weekly_schedule"]),
            "holiday_set_ids": hs_ids,
            "exceptions":     json.loads(r["exceptions"]),
        })
    return result


async def db_get_holidays_for_sets(
    pool: asyncpg.Pool,
    holiday_set_ids: list[str],
) -> list[dict]:
    """Returns all holidays for the given set IDs."""
    if not holiday_set_ids:
        return []
    uuids = [UUID(h) for h in holiday_set_ids]
    rows = await pool.fetch(
        "SELECT id, holidays FROM calendar.holiday_sets WHERE id = ANY($1::uuid[])",
        uuids,
    )
    result = []
    for r in rows:
        result.append({
            "id":       str(r["id"]),
            "holidays": json.loads(r["holidays"]),
        })
    return result
