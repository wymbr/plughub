"""
db.py
DDL and asyncpg operations for the workflow-api.

Table: workflow.instances
  Persists WorkflowInstance state. pipeline_state is the full Skill Flow
  PipelineState serialized as JSONB — reloaded on resume so the engine
  continues from exactly where it was suspended.

Status transitions (enforced by application logic, not DB constraints):
  active → suspended | completed | failed | cancelled
  suspended → active (resume) | timed_out | cancelled
  active | suspended → cancelled
  active → failed
  timed_out / failed / completed / cancelled  → terminal (no further transitions)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger("plughub.workflow.db")

_DDL = """
CREATE SCHEMA IF NOT EXISTS workflow;

CREATE TABLE IF NOT EXISTS workflow.instances (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    installation_id   TEXT        NOT NULL,
    organization_id   TEXT        NOT NULL,
    tenant_id         TEXT        NOT NULL,
    flow_id           TEXT        NOT NULL,
    session_id        TEXT,
    pool_id           TEXT,
    status            TEXT        NOT NULL DEFAULT 'active'
                                  CHECK (status IN ('active','suspended','completed','failed','timed_out','cancelled')),
    current_step      TEXT,
    pipeline_state    JSONB       NOT NULL DEFAULT '{}',
    suspend_reason    TEXT        CHECK (suspend_reason IN ('approval','input','webhook','timer')),
    resume_token      TEXT        UNIQUE,
    resume_expires_at TIMESTAMPTZ,
    suspended_at      TIMESTAMPTZ,
    resumed_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    outcome           TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata          JSONB       NOT NULL DEFAULT '{}'
);

-- Idempotent migration: add outcome column if it was created before this change
ALTER TABLE workflow.instances ADD COLUMN IF NOT EXISTS outcome TEXT;

CREATE INDEX IF NOT EXISTS idx_wf_tenant_status
    ON workflow.instances (tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_wf_resume_token
    ON workflow.instances (resume_token)
    WHERE resume_token IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_wf_expires
    ON workflow.instances (resume_expires_at)
    WHERE status = 'suspended';

CREATE INDEX IF NOT EXISTS idx_wf_session
    ON workflow.instances (tenant_id, session_id)
    WHERE session_id IS NOT NULL;
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DDL)
    logger.info("workflow schema ensured")


# ── Row serialisation ─────────────────────────────────────────────────────────

def _row_to_instance(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":                str(row["id"]),
        "installation_id":   row["installation_id"],
        "organization_id":   row["organization_id"],
        "tenant_id":         row["tenant_id"],
        "flow_id":           row["flow_id"],
        "session_id":        row["session_id"],
        "pool_id":           row["pool_id"],
        "status":            row["status"],
        "current_step":      row["current_step"],
        "pipeline_state":    json.loads(row["pipeline_state"]),
        "suspend_reason":    row["suspend_reason"],
        "resume_token":      row["resume_token"],
        "resume_expires_at": row["resume_expires_at"].isoformat() if row["resume_expires_at"] else None,
        "suspended_at":      row["suspended_at"].isoformat()      if row["suspended_at"]      else None,
        "resumed_at":        row["resumed_at"].isoformat()        if row["resumed_at"]        else None,
        "completed_at":      row["completed_at"].isoformat()      if row["completed_at"]      else None,
        "outcome":           row["outcome"],
        "created_at":        row["created_at"].isoformat(),
        "metadata":          json.loads(row["metadata"]),
    }


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def db_create_instance(pool: asyncpg.Pool, data: dict) -> dict:
    """Create a new WorkflowInstance with status='active'."""
    row = await pool.fetchrow(
        """
        INSERT INTO workflow.instances
            (installation_id, organization_id, tenant_id, flow_id,
             session_id, pool_id, current_step, pipeline_state, metadata)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb)
        RETURNING *
        """,
        data["installation_id"], data["organization_id"], data["tenant_id"],
        data["flow_id"], data.get("session_id"), data.get("pool_id"),
        data.get("current_step"),
        json.dumps(data.get("pipeline_state", {})),
        json.dumps(data.get("metadata", {})),
    )
    return _row_to_instance(row)


async def db_get_instance(pool: asyncpg.Pool, instance_id: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM workflow.instances WHERE id = $1", UUID(instance_id)
    )
    return _row_to_instance(row) if row else None


async def db_get_instance_by_token(pool: asyncpg.Pool, token: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM workflow.instances WHERE resume_token = $1", token
    )
    return _row_to_instance(row) if row else None


async def db_list_instances(
    pool: asyncpg.Pool,
    tenant_id: str,
    status:    str | None = None,
    flow_id:   str | None = None,
    limit:     int = 50,
    offset:    int = 0,
) -> list[dict]:
    filters  = ["tenant_id = $1"]
    params: list[Any] = [tenant_id]

    if status:
        params.append(status)
        filters.append(f"status = ${len(params)}")
    if flow_id:
        params.append(flow_id)
        filters.append(f"flow_id = ${len(params)}")

    params.extend([limit, offset])
    rows = await pool.fetch(
        f"""
        SELECT * FROM workflow.instances
        WHERE {' AND '.join(filters)}
        ORDER BY created_at DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """,
        *params,
    )
    return [_row_to_instance(r) for r in rows]


async def db_suspend_instance(
    pool: asyncpg.Pool,
    instance_id:      str,
    step_id:          str,
    resume_token:     str,
    suspend_reason:   str,
    resume_expires_at: str,        # ISO 8601
    pipeline_state:   dict,
) -> dict | None:
    """
    Transition instance active → suspended.
    Idempotent: if already suspended with the same token, returns the existing row.
    """
    # asyncpg requires a datetime object — parse ISO string
    expires_dt = datetime.fromisoformat(resume_expires_at)
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=timezone.utc)

    row = await pool.fetchrow(
        """
        UPDATE workflow.instances
        SET status            = 'suspended',
            current_step      = $2,
            resume_token      = $3,
            suspend_reason    = $4,
            resume_expires_at = $5,
            suspended_at      = now(),
            pipeline_state    = $6::jsonb
        WHERE id = $1
          AND status IN ('active', 'suspended')
        RETURNING *
        """,
        UUID(instance_id), step_id, resume_token, suspend_reason,
        expires_dt, json.dumps(pipeline_state),
    )
    return _row_to_instance(row) if row else None


async def db_resume_instance(
    pool:         asyncpg.Pool,
    instance_id:  str,
    pipeline_state: dict,
) -> dict | None:
    """Transition instance suspended → active, clear resume fields."""
    row = await pool.fetchrow(
        """
        UPDATE workflow.instances
        SET status         = 'active',
            resume_token   = NULL,
            resumed_at     = now(),
            pipeline_state = $2::jsonb
        WHERE id = $1
          AND status = 'suspended'
        RETURNING *
        """,
        UUID(instance_id), json.dumps(pipeline_state),
    )
    return _row_to_instance(row) if row else None


async def db_complete_instance(
    pool:        asyncpg.Pool,
    instance_id: str,
    outcome:     str,
    pipeline_state: dict,
) -> dict | None:
    row = await pool.fetchrow(
        """
        UPDATE workflow.instances
        SET status         = 'completed',
            completed_at   = now(),
            outcome        = $2,
            pipeline_state = $3::jsonb
        WHERE id = $1
          AND status = 'active'
        RETURNING *
        """,
        UUID(instance_id), outcome, json.dumps(pipeline_state),
    )
    return _row_to_instance(row) if row else None


async def db_fail_instance(
    pool:        asyncpg.Pool,
    instance_id: str,
    error:       str,
) -> dict | None:
    row = await pool.fetchrow(
        """
        UPDATE workflow.instances
        SET status       = 'failed',
            current_step = $2
        WHERE id = $1
          AND status IN ('active', 'suspended')
        RETURNING *
        """,
        UUID(instance_id), error,
    )
    return _row_to_instance(row) if row else None


async def db_cancel_instance(
    pool:        asyncpg.Pool,
    instance_id: str,
) -> dict | None:
    row = await pool.fetchrow(
        """
        UPDATE workflow.instances
        SET status = 'cancelled'
        WHERE id = $1
          AND status IN ('active', 'suspended')
        RETURNING *
        """,
        UUID(instance_id),
    )
    return _row_to_instance(row) if row else None


async def db_timeout_expired_instances(
    pool: asyncpg.Pool,
) -> list[dict]:
    """
    Find all suspended instances whose deadline has passed.
    Atomically marks them timed_out (so concurrent scanners don't double-process).
    Returns the timed-out rows.
    """
    rows = await pool.fetch(
        """
        UPDATE workflow.instances
        SET status = 'timed_out'
        WHERE status = 'suspended'
          AND resume_expires_at < now()
        RETURNING *
        """
    )
    return [_row_to_instance(r) for r in rows]
