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
    origin_session_id TEXT,
    pool_id           TEXT,
    campaign_id       TEXT,
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

-- Idempotent migrations
ALTER TABLE workflow.instances ADD COLUMN IF NOT EXISTS outcome TEXT;
ALTER TABLE workflow.instances ADD COLUMN IF NOT EXISTS campaign_id TEXT;
ALTER TABLE workflow.instances ADD COLUMN IF NOT EXISTS origin_session_id TEXT;

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

CREATE INDEX IF NOT EXISTS idx_wf_campaign
    ON workflow.instances (tenant_id, campaign_id)
    WHERE campaign_id IS NOT NULL;

-- ── collect_instances ─────────────────────────────────────────────────────────
-- One row per collect step execution. Tracks the full lifecycle of an outbound
-- data-collection request: requested → sent → responded | timed_out.

CREATE TABLE IF NOT EXISTS workflow.collect_instances (
    collect_token   TEXT        PRIMARY KEY,
    instance_id     UUID        NOT NULL REFERENCES workflow.instances(id),
    tenant_id       TEXT        NOT NULL,
    flow_id         TEXT        NOT NULL,
    campaign_id     TEXT,
    step_id         TEXT        NOT NULL,
    target_type     TEXT        NOT NULL,
    target_id       TEXT        NOT NULL,
    channel         TEXT        NOT NULL,
    interaction     TEXT        NOT NULL,
    prompt          TEXT        NOT NULL,
    options_json    JSONB       NOT NULL DEFAULT '[]',
    fields_json     JSONB       NOT NULL DEFAULT '[]',
    status          TEXT        NOT NULL DEFAULT 'requested'
                                CHECK (status IN ('requested','sent','responded','timed_out')),
    send_at         TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    responded_at    TIMESTAMPTZ,
    response_data   JSONB       NOT NULL DEFAULT '{}',
    elapsed_ms      BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ci_instance
    ON workflow.collect_instances (instance_id);

CREATE INDEX IF NOT EXISTS idx_ci_tenant_status
    ON workflow.collect_instances (tenant_id, status);

CREATE INDEX IF NOT EXISTS idx_ci_campaign
    ON workflow.collect_instances (tenant_id, campaign_id)
    WHERE campaign_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ci_expires
    ON workflow.collect_instances (expires_at)
    WHERE status IN ('requested', 'sent');

-- ── webhooks ───────────────────────────────────────────────────────────────────
-- One row per registered webhook endpoint.
-- token_hash = SHA-256(plain_token) — plain token shown once at creation, never stored.
-- token_prefix = first 16 chars of plain token — stored for display/identification only.

CREATE TABLE IF NOT EXISTS workflow.webhooks (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         TEXT        NOT NULL,
    flow_id           TEXT        NOT NULL,
    description       TEXT        NOT NULL DEFAULT '',
    token_hash        TEXT        NOT NULL UNIQUE,
    token_prefix      TEXT        NOT NULL,
    active            BOOL        NOT NULL DEFAULT TRUE,
    trigger_count     BIGINT      NOT NULL DEFAULT 0,
    last_triggered_at TIMESTAMPTZ,
    context_override  JSONB       NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wh_tenant
    ON workflow.webhooks (tenant_id);
CREATE INDEX IF NOT EXISTS idx_wh_token_hash
    ON workflow.webhooks (token_hash);

-- ── webhook_deliveries ─────────────────────────────────────────────────────────
-- Append-only delivery log. Pruned by the caller (keep last N per webhook_id).

CREATE TABLE IF NOT EXISTS workflow.webhook_deliveries (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    webhook_id   UUID        NOT NULL REFERENCES workflow.webhooks(id) ON DELETE CASCADE,
    tenant_id    TEXT        NOT NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_code  INT         NOT NULL,
    payload_hash TEXT        NOT NULL,
    instance_id  UUID,
    error        TEXT,
    latency_ms   INT
);

CREATE INDEX IF NOT EXISTS idx_wd_webhook_ts
    ON workflow.webhook_deliveries (webhook_id, triggered_at DESC);
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_DDL)
    logger.info("workflow schema ensured")


# ── Row serialisation ─────────────────────────────────────────────────────────

def _row_to_instance(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":                  str(row["id"]),
        "installation_id":     row["installation_id"],
        "organization_id":     row["organization_id"],
        "tenant_id":           row["tenant_id"],
        "flow_id":             row["flow_id"],
        "session_id":          row["session_id"],
        "origin_session_id":   row["origin_session_id"],
        "pool_id":             row["pool_id"],
        "campaign_id":         row["campaign_id"],
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


def _row_to_collect(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "collect_token": row["collect_token"],
        "instance_id":   str(row["instance_id"]),
        "tenant_id":     row["tenant_id"],
        "flow_id":       row["flow_id"],
        "campaign_id":   row["campaign_id"],
        "step_id":       row["step_id"],
        "target_type":   row["target_type"],
        "target_id":     row["target_id"],
        "channel":       row["channel"],
        "interaction":   row["interaction"],
        "prompt":        row["prompt"],
        "options":       json.loads(row["options_json"]),
        "fields":        json.loads(row["fields_json"]),
        "status":        row["status"],
        "send_at":       row["send_at"].isoformat(),
        "expires_at":    row["expires_at"].isoformat(),
        "responded_at":  row["responded_at"].isoformat() if row["responded_at"] else None,
        "response_data": json.loads(row["response_data"]),
        "elapsed_ms":    row["elapsed_ms"],
        "created_at":    row["created_at"].isoformat(),
    }


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def db_create_instance(pool: asyncpg.Pool, data: dict) -> dict:
    """Create a new WorkflowInstance with status='active'."""
    row = await pool.fetchrow(
        """
        INSERT INTO workflow.instances
            (installation_id, organization_id, tenant_id, flow_id,
             session_id, origin_session_id, pool_id, current_step,
             pipeline_state, metadata)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb)
        RETURNING *
        """,
        data["installation_id"], data["organization_id"], data["tenant_id"],
        data["flow_id"], data.get("session_id"), data.get("origin_session_id"),
        data.get("pool_id"), data.get("current_step"),
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
    Idempotent on resume_token: if the engine crashes between calling persistSuspend
    and saving expiresKey to pipeline_state, the retry will call this function again
    with the same resume_token. In that case we preserve the original resume_expires_at
    and suspended_at so the deadline does not drift forward on each retry.
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
            -- Preserve existing deadline on retry (same token = same suspend attempt).
            -- On first call resume_token IS NULL so ELSE branch sets the new deadline.
            resume_expires_at = CASE
                WHEN resume_token = $3 THEN resume_expires_at
                ELSE $5
            END,
            -- Do not overwrite the original suspended_at on retry
            suspended_at      = COALESCE(suspended_at, now()),
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


# ── Collect instance CRUD ──────────────────────────────────────────────────────

async def db_create_collect(
    pool:          asyncpg.Pool,
    collect_token: str,
    instance_id:   str,
    tenant_id:     str,
    flow_id:       str,
    campaign_id:   str | None,
    step_id:       str,
    target_type:   str,
    target_id:     str,
    channel:       str,
    interaction:   str,
    prompt:        str,
    options:       list,
    fields:        list,
    send_at:       datetime,
    expires_at:    datetime,
) -> dict:
    """Create a collect_instance with status='requested'."""
    row = await pool.fetchrow(
        """
        INSERT INTO workflow.collect_instances
            (collect_token, instance_id, tenant_id, flow_id, campaign_id,
             step_id, target_type, target_id, channel, interaction, prompt,
             options_json, fields_json, send_at, expires_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::jsonb,$14,$15)
        ON CONFLICT (collect_token) DO NOTHING
        RETURNING *
        """,
        collect_token, UUID(instance_id), tenant_id, flow_id, campaign_id,
        step_id, target_type, target_id, channel, interaction, prompt,
        json.dumps(options), json.dumps(fields), send_at, expires_at,
    )
    if row is None:
        # Idempotent — already exists; fetch and return
        row = await pool.fetchrow(
            "SELECT * FROM workflow.collect_instances WHERE collect_token = $1",
            collect_token,
        )
    return _row_to_collect(row)  # type: ignore[arg-type]


async def db_get_collect_by_token(
    pool:          asyncpg.Pool,
    collect_token: str,
) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM workflow.collect_instances WHERE collect_token = $1",
        collect_token,
    )
    return _row_to_collect(row) if row else None


async def db_list_pending_sends(pool: asyncpg.Pool) -> list[dict]:
    """
    Returns collect_instances that are past their send_at and still 'requested'.
    Used by the scheduler to trigger outbound contact.
    """
    rows = await pool.fetch(
        """
        SELECT * FROM workflow.collect_instances
        WHERE status = 'requested'
          AND send_at <= now()
        ORDER BY send_at
        LIMIT 500
        """
    )
    return [_row_to_collect(r) for r in rows]


async def db_mark_collect_sent(
    pool:          asyncpg.Pool,
    collect_token: str,
) -> dict | None:
    """Transition collect_instance requested → sent."""
    row = await pool.fetchrow(
        """
        UPDATE workflow.collect_instances
        SET status = 'sent'
        WHERE collect_token = $1
          AND status = 'requested'
        RETURNING *
        """,
        collect_token,
    )
    return _row_to_collect(row) if row else None


async def db_complete_collect(
    pool:          asyncpg.Pool,
    collect_token: str,
    response_data: dict,
) -> dict | None:
    """
    Transition collect_instance (requested|sent) → responded.
    Sets responded_at, response_data, and elapsed_ms.
    """
    row = await pool.fetchrow(
        """
        UPDATE workflow.collect_instances
        SET status        = 'responded',
            responded_at  = now(),
            response_data = $2::jsonb,
            elapsed_ms    = EXTRACT(EPOCH FROM (now() - created_at))::BIGINT * 1000
        WHERE collect_token = $1
          AND status IN ('requested', 'sent')
        RETURNING *
        """,
        collect_token, json.dumps(response_data),
    )
    return _row_to_collect(row) if row else None


async def db_timeout_expired_collects(pool: asyncpg.Pool) -> list[dict]:
    """
    Atomically marks collect_instances as timed_out whose expires_at has passed.
    Returns the affected rows so the caller can publish collect.timed_out events
    and trigger on_timeout resume for the parent workflow instances.
    """
    rows = await pool.fetch(
        """
        UPDATE workflow.collect_instances
        SET status = 'timed_out'
        WHERE status IN ('requested', 'sent')
          AND expires_at < now()
        RETURNING *
        """
    )
    return [_row_to_collect(r) for r in rows]


async def db_list_collects_by_campaign(
    pool:        asyncpg.Pool,
    tenant_id:   str,
    campaign_id: str,
    limit:       int = 200,
    offset:      int = 0,
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT * FROM workflow.collect_instances
        WHERE tenant_id   = $1
          AND campaign_id = $2
        ORDER BY created_at DESC
        LIMIT $3 OFFSET $4
        """,
        tenant_id, campaign_id, limit, offset,
    )
    return [_row_to_collect(r) for r in rows]


# ── Webhook CRUD ───────────────────────────────────────────────────────────────

def _row_to_webhook(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":                 str(row["id"]),
        "tenant_id":          row["tenant_id"],
        "flow_id":            row["flow_id"],
        "description":        row["description"],
        "token_prefix":       row["token_prefix"],
        "active":             row["active"],
        "trigger_count":      row["trigger_count"],
        "last_triggered_at":  row["last_triggered_at"].isoformat() if row["last_triggered_at"] else None,
        "context_override":   json.loads(row["context_override"]),
        "created_at":         row["created_at"].isoformat(),
        "updated_at":         row["updated_at"].isoformat(),
    }


def _row_to_delivery(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id":           str(row["id"]),
        "webhook_id":   str(row["webhook_id"]),
        "tenant_id":    row["tenant_id"],
        "triggered_at": row["triggered_at"].isoformat(),
        "status_code":  row["status_code"],
        "payload_hash": row["payload_hash"],
        "instance_id":  str(row["instance_id"]) if row["instance_id"] else None,
        "error":        row["error"],
        "latency_ms":   row["latency_ms"],
    }


async def db_create_webhook(
    pool:             asyncpg.Pool,
    tenant_id:        str,
    flow_id:          str,
    description:      str,
    token_hash:       str,
    token_prefix:     str,
    context_override: dict,
) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO workflow.webhooks
            (tenant_id, flow_id, description, token_hash, token_prefix, context_override)
        VALUES ($1,$2,$3,$4,$5,$6::jsonb)
        RETURNING *
        """,
        tenant_id, flow_id, description, token_hash, token_prefix,
        json.dumps(context_override),
    )
    return _row_to_webhook(row)


async def db_get_webhook(
    pool:       asyncpg.Pool,
    webhook_id: str,
) -> dict | None:
    row = await pool.fetchrow(
        "SELECT * FROM workflow.webhooks WHERE id = $1",
        UUID(webhook_id),
    )
    return _row_to_webhook(row) if row else None


async def db_get_webhook_by_token_hash(
    pool:       asyncpg.Pool,
    token_hash: str,
) -> dict | None:
    """Used during trigger authentication — never exposes token_hash in return value."""
    row = await pool.fetchrow(
        "SELECT * FROM workflow.webhooks WHERE token_hash = $1",
        token_hash,
    )
    return _row_to_webhook(row) if row else None


async def db_list_webhooks(
    pool:      asyncpg.Pool,
    tenant_id: str,
    active:    bool | None = None,
    limit:     int = 50,
    offset:    int = 0,
) -> list[dict]:
    filters: list[str]  = ["tenant_id = $1"]
    params:  list[Any]  = [tenant_id]

    if active is not None:
        params.append(active)
        filters.append(f"active = ${len(params)}")

    params.extend([limit, offset])
    rows = await pool.fetch(
        f"""
        SELECT * FROM workflow.webhooks
        WHERE {' AND '.join(filters)}
        ORDER BY created_at DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """,
        *params,
    )
    return [_row_to_webhook(r) for r in rows]


async def db_update_webhook(
    pool:             asyncpg.Pool,
    webhook_id:       str,
    description:      str | None = None,
    active:           bool | None = None,
    context_override: dict | None = None,
) -> dict | None:
    """Partial update (PATCH) — only provided fields are changed."""
    sets:   list[str] = ["updated_at = now()"]
    params: list[Any] = []

    if description is not None:
        params.append(description)
        sets.append(f"description = ${len(params)}")
    if active is not None:
        params.append(active)
        sets.append(f"active = ${len(params)}")
    if context_override is not None:
        params.append(json.dumps(context_override))
        sets.append(f"context_override = ${len(params)}::jsonb")

    if len(params) == 0:
        # Nothing to update — return current state
        return await db_get_webhook(pool, webhook_id)

    params.append(UUID(webhook_id))
    row = await pool.fetchrow(
        f"""
        UPDATE workflow.webhooks
        SET {', '.join(sets)}
        WHERE id = ${len(params)}
        RETURNING *
        """,
        *params,
    )
    return _row_to_webhook(row) if row else None


async def db_rotate_webhook_token(
    pool:         asyncpg.Pool,
    webhook_id:   str,
    token_hash:   str,
    token_prefix: str,
) -> dict | None:
    """Replace the token hash with a new one (token rotation)."""
    row = await pool.fetchrow(
        """
        UPDATE workflow.webhooks
        SET token_hash   = $2,
            token_prefix = $3,
            updated_at   = now()
        WHERE id = $1
        RETURNING *
        """,
        UUID(webhook_id), token_hash, token_prefix,
    )
    return _row_to_webhook(row) if row else None


async def db_delete_webhook(
    pool:       asyncpg.Pool,
    webhook_id: str,
) -> bool:
    result = await pool.execute(
        "DELETE FROM workflow.webhooks WHERE id = $1",
        UUID(webhook_id),
    )
    return result == "DELETE 1"


async def db_record_delivery(
    pool:         asyncpg.Pool,
    webhook_id:   str,
    tenant_id:    str,
    status_code:  int,
    payload_hash: str,
    instance_id:  str | None = None,
    error:        str | None = None,
    latency_ms:   int | None = None,
) -> dict:
    """
    Append a delivery record and atomically increment the webhook trigger_count
    + update last_triggered_at when the delivery was successful (2xx).
    """
    row = await pool.fetchrow(
        """
        INSERT INTO workflow.webhook_deliveries
            (webhook_id, tenant_id, status_code, payload_hash, instance_id, error, latency_ms)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        RETURNING *
        """,
        UUID(webhook_id), tenant_id, status_code, payload_hash,
        UUID(instance_id) if instance_id else None,
        error, latency_ms,
    )
    if 200 <= status_code < 300:
        await pool.execute(
            """
            UPDATE workflow.webhooks
            SET trigger_count     = trigger_count + 1,
                last_triggered_at = now(),
                updated_at        = now()
            WHERE id = $1
            """,
            UUID(webhook_id),
        )
    return _row_to_delivery(row)


async def db_list_deliveries(
    pool:       asyncpg.Pool,
    webhook_id: str,
    limit:      int = 50,
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT * FROM workflow.webhook_deliveries
        WHERE webhook_id = $1
        ORDER BY triggered_at DESC
        LIMIT $2
        """,
        UUID(webhook_id), limit,
    )
    return [_row_to_delivery(r) for r in rows]
