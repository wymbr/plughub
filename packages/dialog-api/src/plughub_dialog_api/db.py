"""
db.py
DDL and raw asyncpg operations for the dialog-api.

Single table in schema 'dialog':
  dialog.forms — versioned dialog-form JSON (draft/published), PK (tenant_id, form_id, version)

Versioning mirrors EvaluationForm + skill deploy lifecycle:
  - create → version = max(version)+1 (or 1), status='draft'
  - put    → if latest row is draft, replace its json; if published, create a new draft version
  - publish→ set a version's status='published' (the highest published version is "current")
  - get published → highest published version
"""
from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger("plughub.dialog.db")

_DDL_SCHEMA = "CREATE SCHEMA IF NOT EXISTS dialog"

_DDL_FORMS = """
CREATE TABLE IF NOT EXISTS dialog.forms (
    tenant_id   TEXT        NOT NULL,
    form_id     TEXT        NOT NULL,
    version     INTEGER     NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft','published')),
    name        TEXT        NOT NULL DEFAULT '',
    tags        JSONB       NOT NULL DEFAULT '[]',
    json        JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, form_id, version)
)
"""

_DDL_FORMS_IDX = (
    "CREATE INDEX IF NOT EXISTS idx_dialog_forms_lookup "
    "ON dialog.forms (tenant_id, form_id, status, version DESC)"
)


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_DDL_SCHEMA)
            await conn.execute(_DDL_FORMS)
            await conn.execute(_DDL_FORMS_IDX)
    logger.info("dialog schema ensured")


def _row_to_form(row: asyncpg.Record) -> dict[str, Any]:
    """Return the stored DialogForm JSON, with authoritative row columns merged in."""
    doc = json.loads(row["json"])
    doc["tenant_id"] = row["tenant_id"]
    doc["form_id"]   = row["form_id"]
    doc["version"]   = row["version"]
    doc["status"]    = row["status"]
    doc["created_at"] = row["created_at"].isoformat()
    doc["updated_at"] = row["updated_at"].isoformat()
    return doc


def _row_to_meta(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "tenant_id":  row["tenant_id"],
        "form_id":    row["form_id"],
        "version":    row["version"],
        "status":     row["status"],
        "name":       row["name"],
        "tags":       json.loads(row["tags"]),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


async def db_list_forms(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    """List the latest version (metadata only) per form_id for a tenant."""
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (form_id)
               tenant_id, form_id, version, status, name, tags, created_at, updated_at
        FROM dialog.forms
        WHERE tenant_id = $1
        ORDER BY form_id, version DESC
        """,
        tenant_id,
    )
    return [_row_to_meta(r) for r in rows]


async def db_get_form(
    pool: asyncpg.Pool,
    tenant_id: str,
    form_id: str,
    *,
    status: str | None = None,
    version: int | None = None,
) -> dict | None:
    """
    Resolve a single dialog form.
      version given   → that exact (form_id, version)
      status='published' → highest published version (the "current")
      else            → highest version regardless of status
    """
    if version is not None:
        row = await pool.fetchrow(
            "SELECT * FROM dialog.forms WHERE tenant_id=$1 AND form_id=$2 AND version=$3",
            tenant_id, form_id, version,
        )
    elif status == "published":
        row = await pool.fetchrow(
            """
            SELECT * FROM dialog.forms
            WHERE tenant_id=$1 AND form_id=$2 AND status='published'
            ORDER BY version DESC LIMIT 1
            """,
            tenant_id, form_id,
        )
    else:
        row = await pool.fetchrow(
            """
            SELECT * FROM dialog.forms
            WHERE tenant_id=$1 AND form_id=$2
            ORDER BY version DESC LIMIT 1
            """,
            tenant_id, form_id,
        )
    return _row_to_form(row) if row else None


async def _next_version(conn: asyncpg.Connection, tenant_id: str, form_id: str) -> int:
    maxv = await conn.fetchval(
        "SELECT max(version) FROM dialog.forms WHERE tenant_id=$1 AND form_id=$2",
        tenant_id, form_id,
    )
    return (maxv or 0) + 1


async def db_create_form(pool: asyncpg.Pool, tenant_id: str, doc: dict) -> dict:
    """Create a new draft version of a form (version = max+1)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            version = await _next_version(conn, tenant_id, doc["form_id"])
            stored = dict(doc)
            stored.update({"tenant_id": tenant_id, "version": version, "status": "draft"})
            row = await conn.fetchrow(
                """
                INSERT INTO dialog.forms (tenant_id, form_id, version, status, name, tags, json)
                VALUES ($1,$2,$3,'draft',$4,$5::jsonb,$6::jsonb)
                RETURNING *
                """,
                tenant_id, doc["form_id"], version,
                doc.get("name", ""), json.dumps(doc.get("tags", [])),
                json.dumps(stored),
            )
    return _row_to_form(row)


async def db_put_form(pool: asyncpg.Pool, tenant_id: str, form_id: str, doc: dict) -> dict:
    """
    Edit a form. If the latest version is a draft, replace its json in place;
    if the latest is published (or none exists), create a new draft version.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            latest = await conn.fetchrow(
                """
                SELECT version, status FROM dialog.forms
                WHERE tenant_id=$1 AND form_id=$2
                ORDER BY version DESC LIMIT 1
                """,
                tenant_id, form_id,
            )
            if latest is not None and latest["status"] == "draft":
                version = latest["version"]
                stored = dict(doc)
                stored.update({"tenant_id": tenant_id, "form_id": form_id,
                               "version": version, "status": "draft"})
                row = await conn.fetchrow(
                    """
                    UPDATE dialog.forms
                    SET name=$4, tags=$5::jsonb, json=$6::jsonb, updated_at=now()
                    WHERE tenant_id=$1 AND form_id=$2 AND version=$3
                    RETURNING *
                    """,
                    tenant_id, form_id, version,
                    doc.get("name", ""), json.dumps(doc.get("tags", [])),
                    json.dumps(stored),
                )
            else:
                version = (latest["version"] + 1) if latest is not None else 1
                stored = dict(doc)
                stored.update({"tenant_id": tenant_id, "form_id": form_id,
                               "version": version, "status": "draft"})
                row = await conn.fetchrow(
                    """
                    INSERT INTO dialog.forms (tenant_id, form_id, version, status, name, tags, json)
                    VALUES ($1,$2,$3,'draft',$4,$5::jsonb,$6::jsonb)
                    RETURNING *
                    """,
                    tenant_id, form_id, version,
                    doc.get("name", ""), json.dumps(doc.get("tags", [])),
                    json.dumps(stored),
                )
    return _row_to_form(row)


async def db_publish_form(
    pool: asyncpg.Pool,
    tenant_id: str,
    form_id: str,
    version: int | None = None,
) -> dict | None:
    """Publish a version (default = the latest draft). Idempotent snapshot."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            if version is None:
                target = await conn.fetchval(
                    """
                    SELECT version FROM dialog.forms
                    WHERE tenant_id=$1 AND form_id=$2 AND status='draft'
                    ORDER BY version DESC LIMIT 1
                    """,
                    tenant_id, form_id,
                )
                if target is None:
                    return None
                version = target
            row = await conn.fetchrow(
                """
                UPDATE dialog.forms
                SET status='published', updated_at=now()
                WHERE tenant_id=$1 AND form_id=$2 AND version=$3
                RETURNING *
                """,
                tenant_id, form_id, version,
            )
    return _row_to_form(row) if row else None
