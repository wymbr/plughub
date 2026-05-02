"""
db.py
asyncpg DDL + CRUD for the evaluation module.

Schema: evaluation (dedicated PostgreSQL schema)

Tables:
  evaluation.forms             — EvaluationForm definitions with JSONB dimensions + criteria
  evaluation.campaigns         — EvaluationCampaign: links form + pool, sampling + reviewer rules
  evaluation.instances         — EvaluationInstance: one per session scheduled for evaluation
  evaluation.results           — EvaluationResult: the evaluator agent's output per instance
  evaluation.criterion_responses — per-criterion scores/values with evidence refs
  evaluation.contestations     — human contestation workflow

All timestamps are TIMESTAMPTZ.
All IDs are TEXT (UUIDs as strings, prefixed e.g. "evform_", "evcampaign_", "evinstance_").
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

import asyncpg

logger = logging.getLogger("plughub.evaluation.db")

# ─── DDL ──────────────────────────────────────────────────────────────────────

_DDL = """
CREATE SCHEMA IF NOT EXISTS evaluation;

-- ── EvaluationForm ────────────────────────────────────────────────────────────
-- Stores the full form definition as JSONB (dimensions → criteria hierarchy).
CREATE TABLE IF NOT EXISTS evaluation.forms (
    id              TEXT        PRIMARY KEY,            -- "evform_{uuid}"
    tenant_id       TEXT        NOT NULL,
    name            TEXT        NOT NULL,
    description     TEXT        NOT NULL DEFAULT '',
    version         INTEGER     NOT NULL DEFAULT 1,
    status          TEXT        NOT NULL DEFAULT 'active'
                    CHECK (status IN ('draft', 'active', 'archived')),
    dimensions      JSONB       NOT NULL DEFAULT '[]',  -- EvaluationDimension[]
    total_weight    NUMERIC(6,3) NOT NULL DEFAULT 1.0,
    passing_score   NUMERIC(6,3),                       -- NULL = no minimum
    allow_na        BOOLEAN     NOT NULL DEFAULT TRUE,
    knowledge_domains TEXT[]    NOT NULL DEFAULT '{}',  -- namespaces used for RAG
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT        NOT NULL DEFAULT 'operator'
);

CREATE INDEX IF NOT EXISTS idx_evforms_tenant
    ON evaluation.forms (tenant_id, status);

-- ── EvaluationCampaign ────────────────────────────────────────────────────────
-- Links a form to a pool; controls sampling, scheduling, and reviewer rules.
CREATE TABLE IF NOT EXISTS evaluation.campaigns (
    id                  TEXT        PRIMARY KEY,         -- "evcampaign_{uuid}"
    tenant_id           TEXT        NOT NULL,
    name                TEXT        NOT NULL,
    description         TEXT        NOT NULL DEFAULT '',
    form_id             TEXT        NOT NULL REFERENCES evaluation.forms(id),
    pool_id             TEXT        NOT NULL,
    status              TEXT        NOT NULL DEFAULT 'active'
                        CHECK (status IN ('draft', 'active', 'paused', 'closed')),
    -- Sampling rules (JSONB — SamplingRules schema)
    sampling_rules      JSONB       NOT NULL DEFAULT '{}',
    -- Reviewer rules (JSONB — ReviewerRules schema)
    reviewer_rules      JSONB       NOT NULL DEFAULT '{}',
    -- Scheduling (JSONB — CampaignSchedule schema: window_start, window_end, days_of_week)
    schedule            JSONB       NOT NULL DEFAULT '{}',
    -- Stats (denormalised counters, updated by results insert)
    total_instances     INTEGER     NOT NULL DEFAULT 0,
    completed_instances INTEGER     NOT NULL DEFAULT 0,
    avg_score           NUMERIC(6,3),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by          TEXT        NOT NULL DEFAULT 'operator'
);

CREATE INDEX IF NOT EXISTS idx_evcampaigns_tenant
    ON evaluation.campaigns (tenant_id, pool_id, status);

CREATE INDEX IF NOT EXISTS idx_evcampaigns_form
    ON evaluation.campaigns (form_id);

-- ── EvaluationInstance ────────────────────────────────────────────────────────
-- One record per session scheduled for evaluation.
-- Status lifecycle: scheduled → assigned → in_progress → completed | expired | error
CREATE TABLE IF NOT EXISTS evaluation.instances (
    id                  TEXT        PRIMARY KEY,         -- "evinstance_{uuid}"
    tenant_id           TEXT        NOT NULL,
    campaign_id         TEXT        NOT NULL REFERENCES evaluation.campaigns(id),
    form_id             TEXT        NOT NULL REFERENCES evaluation.forms(id),
    session_id          TEXT        NOT NULL,
    segment_id          TEXT,                            -- ContactSegment for the evaluated agent
    evaluator_agent_id  TEXT,                            -- instance_id of the assigned evaluator
    reviewer_agent_id   TEXT,                            -- instance_id of assigned reviewer (if any)
    status              TEXT        NOT NULL DEFAULT 'scheduled'
                        CHECK (status IN (
                            'scheduled', 'assigned', 'in_progress',
                            'completed', 'under_review', 'reviewed',
                            'contested', 'locked', 'expired', 'error'
                        )),
    priority            INTEGER     NOT NULL DEFAULT 5,  -- 1=highest, 10=lowest
    scheduled_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_at         TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ,
    error_message       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evinstances_tenant_status
    ON evaluation.instances (tenant_id, status, scheduled_at);

CREATE INDEX IF NOT EXISTS idx_evinstances_session
    ON evaluation.instances (session_id);

CREATE INDEX IF NOT EXISTS idx_evinstances_campaign
    ON evaluation.instances (campaign_id, status);

-- ── EvaluationResult ─────────────────────────────────────────────────────────
-- The evaluator agent's final output for one instance.
-- One result per instance (UNIQUE constraint).
CREATE TABLE IF NOT EXISTS evaluation.results (
    id                  TEXT        PRIMARY KEY,         -- "evresult_{uuid}"
    tenant_id           TEXT        NOT NULL,
    instance_id         TEXT        NOT NULL REFERENCES evaluation.instances(id),
    session_id          TEXT        NOT NULL,
    campaign_id         TEXT        NOT NULL,
    form_id             TEXT        NOT NULL,
    evaluator_agent_id  TEXT        NOT NULL,
    -- Scores
    overall_score       NUMERIC(6,3),
    max_score           NUMERIC(6,3),
    normalized_score    NUMERIC(6,3),                    -- 0–1
    passed              BOOLEAN,                         -- NULL if no passing_score on form
    -- Evaluation metadata
    eval_status         TEXT        NOT NULL DEFAULT 'submitted'
                        CHECK (eval_status IN ('submitted', 'under_review', 'reviewed', 'contested', 'locked')),
    evaluator_notes     TEXT        NOT NULL DEFAULT '',
    comparison_mode     BOOLEAN     NOT NULL DEFAULT FALSE,
    comparison_report   JSONB,                           -- ComparisonReport if comparison_mode
    knowledge_snippets  JSONB       NOT NULL DEFAULT '[]', -- KnowledgeSnippet[] used
    -- Reviewer outcome
    reviewer_agent_id   TEXT,
    reviewer_outcome    TEXT        CHECK (reviewer_outcome IN ('approved', 'adjusted', 'rejected', NULL)),
    reviewer_notes      TEXT,
    reviewer_score      NUMERIC(6,3),
    reviewed_at         TIMESTAMPTZ,
    -- Contestation
    contested_by        TEXT,
    contested_at        TIMESTAMPTZ,
    contestation_reason TEXT,
    locked_at           TIMESTAMPTZ,
    locked_by           TEXT,
    -- Timing
    submitted_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_result_per_instance UNIQUE (instance_id)
);

CREATE INDEX IF NOT EXISTS idx_evresults_tenant
    ON evaluation.results (tenant_id, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_evresults_campaign
    ON evaluation.results (campaign_id, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_evresults_session
    ON evaluation.results (session_id);

-- ── EvaluationCriterionResponse ───────────────────────────────────────────────
-- Per-criterion scores for one result.
CREATE TABLE IF NOT EXISTS evaluation.criterion_responses (
    id              TEXT        PRIMARY KEY,             -- "evcrr_{uuid}"
    result_id       TEXT        NOT NULL REFERENCES evaluation.results(id) ON DELETE CASCADE,
    instance_id     TEXT        NOT NULL,
    campaign_id     TEXT        NOT NULL,
    tenant_id       TEXT        NOT NULL,
    criterion_id    TEXT        NOT NULL,
    criterion_name  TEXT        NOT NULL DEFAULT '',
    dimension_id    TEXT        NOT NULL DEFAULT '',
    na              BOOLEAN     NOT NULL DEFAULT FALSE,
    score           NUMERIC(6,3),
    max_score       NUMERIC(6,3),
    boolean_value   BOOLEAN,
    choice_value    TEXT,
    text_value      TEXT,
    notes           TEXT,
    evidence        JSONB       NOT NULL DEFAULT '[]',   -- EvidenceRef[]
    weight          NUMERIC(6,3) NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evcrr_result
    ON evaluation.criterion_responses (result_id);

CREATE INDEX IF NOT EXISTS idx_evcrr_campaign_criterion
    ON evaluation.criterion_responses (campaign_id, criterion_id);

-- ── EvaluationContestation ────────────────────────────────────────────────────
-- Human agent contests an evaluation result; supervisor reviews.
CREATE TABLE IF NOT EXISTS evaluation.contestations (
    id                  TEXT        PRIMARY KEY,         -- "evcontest_{uuid}"
    tenant_id           TEXT        NOT NULL,
    result_id           TEXT        NOT NULL REFERENCES evaluation.results(id),
    instance_id         TEXT        NOT NULL,
    session_id          TEXT        NOT NULL,
    -- Who filed
    contested_by        TEXT        NOT NULL,            -- agent instance_id or user_id
    contested_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    contestation_reason TEXT        NOT NULL DEFAULT '',
    -- Adjudication
    status              TEXT        NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'under_review', 'accepted', 'rejected', 'withdrawn')),
    adjudicated_by      TEXT,
    adjudicated_at      TIMESTAMPTZ,
    adjudication_notes  TEXT,
    adjusted_score      NUMERIC(6,3),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_evcontest_result
    ON evaluation.contestations (result_id);

CREATE INDEX IF NOT EXISTS idx_evcontest_tenant_status
    ON evaluation.contestations (tenant_id, status, contested_at DESC);

-- ── Arc 6 v2 migrations (idempotent ALTER TABLE) ──────────────────────────────

-- evaluation.campaigns: workflow skill reference + contestation policy
ALTER TABLE evaluation.campaigns
    ADD COLUMN IF NOT EXISTS review_workflow_skill_id TEXT,
    ADD COLUMN IF NOT EXISTS contestation_policy JSONB NOT NULL DEFAULT '{}';

-- evaluation.results: workflow motor state tracking
ALTER TABLE evaluation.results
    ADD COLUMN IF NOT EXISTS workflow_instance_id TEXT,
    ADD COLUMN IF NOT EXISTS resume_token TEXT,
    ADD COLUMN IF NOT EXISTS action_required TEXT
        CHECK (action_required IN ('review', 'contestation')),
    ADD COLUMN IF NOT EXISTS current_round INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS deadline_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS lock_reason TEXT;

-- evaluation.contestations: round + authority tracking
ALTER TABLE evaluation.contestations
    ADD COLUMN IF NOT EXISTS round_number INT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS authority_level TEXT;

-- evaluation.permissions table removed: permissions are now handled via
-- ABAC module_config in the auth-api JWT (module_config.evaluation.revisar /
-- module_config.evaluation.contestar). Drop the legacy table if it exists.
DROP TABLE IF EXISTS evaluation.permissions;
"""


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(_DDL)
    logger.info("evaluation schema ensured")


# ─── Helper ───────────────────────────────────────────────────────────────────

def _parse_jsonb(val: Any) -> Any:
    """asyncpg returns JSONB columns as raw strings — parse them back to Python objects."""
    if isinstance(val, str) and val and val[0] in ('{', '['):
        try:
            import json as _json
            return _json.loads(val)
        except Exception:
            pass
    return val


def _row(record: asyncpg.Record | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {k: _parse_jsonb(v) for k, v in dict(record).items()}


def _rows(records: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [_row(r) for r in records]  # type: ignore[misc]


def _new_id(prefix: str) -> str:
    return f"{prefix}{uuid4().hex}"


# ─── Forms CRUD ───────────────────────────────────────────────────────────────

async def create_form(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    description: str = "",
    dimensions: list[dict] | None = None,
    total_weight: float = 1.0,
    passing_score: float | None = None,
    allow_na: bool = True,
    knowledge_domains: list[str] | None = None,
    created_by: str = "operator",
) -> dict[str, Any]:
    form_id = _new_id("evform_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.forms
                (id, tenant_id, name, description, dimensions, total_weight,
                 passing_score, allow_na, knowledge_domains, created_by)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10)
            RETURNING *
            """,
            form_id, tenant_id, name, description,
            json.dumps(dimensions or []),
            total_weight, passing_score, allow_na,
            knowledge_domains or [], created_by,
        )
    return _row(row)  # type: ignore[return-value]


async def get_form(pool: asyncpg.Pool, form_id: str, tenant_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.forms WHERE id=$1 AND tenant_id=$2",
            form_id, tenant_id,
        )
    return _row(row)


async def list_forms(
    pool: asyncpg.Pool,
    tenant_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cond = "WHERE tenant_id=$1"
    args: list[Any] = [tenant_id]
    if status:
        cond += " AND status=$2"
        args.append(status)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM evaluation.forms {cond} ORDER BY created_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, limit, offset,
        )
    return _rows(rows)


async def update_form(
    pool: asyncpg.Pool,
    form_id: str,
    tenant_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    allowed = {"name", "description", "dimensions", "total_weight", "passing_score",
               "allow_na", "knowledge_domains", "status"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return await get_form(pool, form_id, tenant_id)

    set_parts = []
    args: list[Any] = []
    idx = 1
    for k, v in updates.items():
        if k == "dimensions":
            set_parts.append(f"{k}=${idx}::jsonb")
            args.append(json.dumps(v))
        else:
            set_parts.append(f"{k}=${idx}")
            args.append(v)
        idx += 1
    set_parts.append(f"updated_at=${idx}")
    args.append(datetime.utcnow())
    idx += 1
    args.extend([form_id, tenant_id])

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE evaluation.forms SET {', '.join(set_parts)} "
            f"WHERE id=${idx} AND tenant_id=${idx+1} RETURNING *",
            *args,
        )
    return _row(row)


async def delete_form(pool: asyncpg.Pool, form_id: str, tenant_id: str) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM evaluation.forms WHERE id=$1 AND tenant_id=$2",
            form_id, tenant_id,
        )
    return result.split()[-1] != "0"


# ─── Campaigns CRUD ───────────────────────────────────────────────────────────

async def create_campaign(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    description: str = "",
    form_id: str,
    pool_id: str,
    sampling_rules: dict | None = None,
    reviewer_rules: dict | None = None,
    schedule: dict | None = None,
    created_by: str = "operator",
) -> dict[str, Any]:
    campaign_id = _new_id("evcampaign_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.campaigns
                (id, tenant_id, name, description, form_id, pool_id,
                 sampling_rules, reviewer_rules, schedule, created_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10)
            RETURNING *
            """,
            campaign_id, tenant_id, name, description, form_id, pool_id,
            json.dumps(sampling_rules or {}),
            json.dumps(reviewer_rules or {}),
            json.dumps(schedule or {}),
            created_by,
        )
    return _row(row)  # type: ignore[return-value]


async def get_campaign(pool: asyncpg.Pool, campaign_id: str, tenant_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.campaigns WHERE id=$1 AND tenant_id=$2",
            campaign_id, tenant_id,
        )
    return _row(row)


async def list_campaigns(
    pool: asyncpg.Pool,
    tenant_id: str,
    pool_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cond = "WHERE tenant_id=$1"
    args: list[Any] = [tenant_id]
    if pool_id:
        args.append(pool_id)
        cond += f" AND pool_id=${len(args)}"
    if status:
        args.append(status)
        cond += f" AND status=${len(args)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM evaluation.campaigns {cond} ORDER BY created_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, limit, offset,
        )
    return _rows(rows)


async def update_campaign(
    pool: asyncpg.Pool,
    campaign_id: str,
    tenant_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    allowed = {"name", "description", "status", "sampling_rules", "reviewer_rules", "schedule",
               "total_instances", "completed_instances", "avg_score",
               "review_workflow_skill_id", "contestation_policy"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return await get_campaign(pool, campaign_id, tenant_id)

    set_parts = []
    args: list[Any] = []
    idx = 1
    jsonb_fields = {"sampling_rules", "reviewer_rules", "schedule", "contestation_policy"}
    for k, v in updates.items():
        if k in jsonb_fields:
            set_parts.append(f"{k}=${idx}::jsonb")
            args.append(json.dumps(v))
        else:
            set_parts.append(f"{k}=${idx}")
            args.append(v)
        idx += 1
    set_parts.append(f"updated_at=${idx}")
    args.append(datetime.utcnow())
    idx += 1
    args.extend([campaign_id, tenant_id])

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE evaluation.campaigns SET {', '.join(set_parts)} "
            f"WHERE id=${idx} AND tenant_id=${idx+1} RETURNING *",
            *args,
        )
    return _row(row)


# ─── Instances CRUD ───────────────────────────────────────────────────────────

async def create_instance(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    campaign_id: str,
    form_id: str,
    session_id: str,
    segment_id: str | None = None,
    priority: int = 5,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    instance_id = _new_id("evinstance_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.instances
                (id, tenant_id, campaign_id, form_id, session_id, segment_id, priority, expires_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            RETURNING *
            """,
            instance_id, tenant_id, campaign_id, form_id, session_id, segment_id, priority, expires_at,
        )
        # increment campaign counter
        await conn.execute(
            "UPDATE evaluation.campaigns SET total_instances=total_instances+1, updated_at=now() WHERE id=$1",
            campaign_id,
        )
    return _row(row)  # type: ignore[return-value]


async def get_instance(pool: asyncpg.Pool, instance_id: str, tenant_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.instances WHERE id=$1 AND tenant_id=$2",
            instance_id, tenant_id,
        )
    return _row(row)


async def list_instances(
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cond = "WHERE tenant_id=$1"
    args: list[Any] = [tenant_id]
    for col, val in [("campaign_id", campaign_id), ("status", status), ("session_id", session_id)]:
        if val is not None:
            args.append(val)
            cond += f" AND {col}=${len(args)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM evaluation.instances {cond} ORDER BY scheduled_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, limit, offset,
        )
    return _rows(rows)


async def update_instance_status(
    pool: asyncpg.Pool,
    instance_id: str,
    tenant_id: str,
    status: str,
    **extra_fields: Any,
) -> dict[str, Any] | None:
    allowed_extra = {"evaluator_agent_id", "reviewer_agent_id", "assigned_at",
                     "completed_at", "error_message", "expires_at"}
    set_parts = ["status=$1", "updated_at=$2"]
    args: list[Any] = [status, datetime.utcnow()]
    for k, v in extra_fields.items():
        if k in allowed_extra:
            args.append(v)
            set_parts.append(f"{k}=${len(args)}")
    args.extend([instance_id, tenant_id])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE evaluation.instances SET {', '.join(set_parts)} "
            f"WHERE id=${len(args)-1} AND tenant_id=${len(args)} RETURNING *",
            *args,
        )
    return _row(row)


async def claim_next_instance(
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str | None = None,
    evaluator_agent_id: str | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the next scheduled instance (highest priority first)."""
    cond = "WHERE tenant_id=$1 AND status='scheduled'"
    args: list[Any] = [tenant_id]
    if campaign_id:
        args.append(campaign_id)
        cond += f" AND campaign_id=${len(args)}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE evaluation.instances
               SET status='assigned',
                   evaluator_agent_id=$2,
                   assigned_at=now(),
                   updated_at=now()
             WHERE id = (
               SELECT id FROM evaluation.instances {cond}
                 AND (expires_at IS NULL OR expires_at > now())
               ORDER BY priority ASC, scheduled_at ASC
               LIMIT 1
               FOR UPDATE SKIP LOCKED
             )
            RETURNING *
            """,
            tenant_id, evaluator_agent_id, *args[1:],
        )
    return _row(row)


# ─── Results CRUD ─────────────────────────────────────────────────────────────

async def create_result(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    instance_id: str,
    session_id: str,
    campaign_id: str,
    form_id: str,
    evaluator_agent_id: str,
    overall_score: float | None = None,
    max_score: float | None = None,
    normalized_score: float | None = None,
    passed: bool | None = None,
    eval_status: str = "submitted",
    evaluator_notes: str = "",
    comparison_mode: bool = False,
    comparison_report: dict | None = None,
    knowledge_snippets: list[dict] | None = None,
) -> dict[str, Any]:
    result_id = _new_id("evresult_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.results
                (id, tenant_id, instance_id, session_id, campaign_id, form_id,
                 evaluator_agent_id, overall_score, max_score, normalized_score, passed,
                 eval_status, evaluator_notes, comparison_mode, comparison_report, knowledge_snippets)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16::jsonb)
            RETURNING *
            """,
            result_id, tenant_id, instance_id, session_id, campaign_id, form_id,
            evaluator_agent_id, overall_score, max_score, normalized_score, passed,
            eval_status, evaluator_notes, comparison_mode,
            json.dumps(comparison_report) if comparison_report else None,
            json.dumps(knowledge_snippets or []),
        )
        # update campaign stats
        await conn.execute(
            """
            UPDATE evaluation.campaigns
               SET completed_instances = completed_instances + 1,
                   avg_score = (
                     SELECT AVG(overall_score) FROM evaluation.results
                      WHERE campaign_id = $1 AND overall_score IS NOT NULL
                   ),
                   updated_at = now()
             WHERE id = $1
            """,
            campaign_id,
        )
        # advance instance to completed
        await conn.execute(
            "UPDATE evaluation.instances SET status='completed', completed_at=now(), updated_at=now() WHERE id=$1",
            instance_id,
        )
    return _row(row)  # type: ignore[return-value]


async def get_result(pool: asyncpg.Pool, result_id: str, tenant_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.results WHERE id=$1 AND tenant_id=$2",
            result_id, tenant_id,
        )
    return _row(row)


async def get_result_by_id(pool: asyncpg.Pool, result_id: str) -> dict[str, Any] | None:
    """Look up a result by ID only — used when tenant_id is not available (e.g. lock endpoint)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM evaluation.results WHERE id=$1", result_id)
    return _row(row)


async def get_result_by_instance(pool: asyncpg.Pool, instance_id: str, tenant_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.results WHERE instance_id=$1 AND tenant_id=$2",
            instance_id, tenant_id,
        )
    return _row(row)


async def list_results(
    pool: asyncpg.Pool,
    tenant_id: str,
    campaign_id: str | None = None,
    session_id: str | None = None,
    eval_status: str | None = None,
    action_required: str | None = None,   # "review" | "contestation" | "any" (non-null)
    pool_id: str | None = None,            # filter via campaign → pool
    evaluator_id: str | None = None,
    locked: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if pool_id:
        # Join through campaigns to filter by pool_id
        base = """
            SELECT r.*
            FROM evaluation.results r
            LEFT JOIN evaluation.campaigns c ON c.id = r.campaign_id
            WHERE r.tenant_id=$1
        """
        cond_prefix = "AND"
    else:
        base = "SELECT * FROM evaluation.results WHERE tenant_id=$1"
        cond_prefix = "AND"

    cond = ""
    args: list[Any] = [tenant_id]

    for col, val in [
        ("r.campaign_id" if pool_id else "campaign_id", campaign_id),
        ("r.session_id"  if pool_id else "session_id",  session_id),
        ("r.eval_status" if pool_id else "eval_status",  eval_status),
        ("r.evaluator_id" if pool_id else "evaluator_id", evaluator_id),
    ]:
        if val is not None:
            args.append(val)
            cond += f" {cond_prefix} {col}=${len(args)}"

    if pool_id:
        args.append(pool_id)
        cond += f" {cond_prefix} c.pool_id=${len(args)}"

    if action_required == "any":
        cond += f" {cond_prefix} {'r.' if pool_id else ''}action_required IS NOT NULL"
    elif action_required is not None:
        args.append(action_required)
        cond += f" {cond_prefix} {'r.' if pool_id else ''}action_required=${len(args)}"

    if locked is not None:
        args.append(locked)
        cond += f" {cond_prefix} {'r.' if pool_id else ''}locked=${len(args)}"

    order_col = "r.submitted_at" if pool_id else "submitted_at"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"{base}{cond} ORDER BY {order_col} DESC NULLS LAST"
            f" LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, limit, offset,
        )
    return _rows(rows)


async def update_result(
    pool: asyncpg.Pool,
    result_id: str,
    tenant_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    allowed = {"eval_status", "reviewer_agent_id", "reviewer_outcome", "reviewer_notes",
               "reviewer_score", "reviewed_at", "contested_by", "contested_at",
               "contestation_reason", "locked_at", "locked_by"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return await get_result(pool, result_id, tenant_id)
    set_parts = []
    args: list[Any] = []
    idx = 1
    for k, v in updates.items():
        set_parts.append(f"{k}=${idx}")
        args.append(v)
        idx += 1
    set_parts.append(f"updated_at=${idx}")
    args.append(datetime.utcnow())
    idx += 1
    args.extend([result_id, tenant_id])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE evaluation.results SET {', '.join(set_parts)} "
            f"WHERE id=${idx} AND tenant_id=${idx+1} RETURNING *",
            *args,
        )
    return _row(row)


# ─── CriterionResponses ───────────────────────────────────────────────────────

async def create_criterion_responses(
    pool: asyncpg.Pool,
    result_id: str,
    instance_id: str,
    campaign_id: str,
    tenant_id: str,
    responses: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not responses:
        return []
    rows = []
    async with pool.acquire() as conn:
        for r in responses:
            row = await conn.fetchrow(
                """
                INSERT INTO evaluation.criterion_responses
                    (id, result_id, instance_id, campaign_id, tenant_id,
                     criterion_id, criterion_name, dimension_id,
                     na, score, max_score, boolean_value, choice_value,
                     text_value, notes, evidence, weight)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16::jsonb,$17)
                RETURNING *
                """,
                _new_id("evcrr_"), result_id, instance_id, campaign_id, tenant_id,
                r.get("criterion_id", ""), r.get("criterion_name", ""), r.get("dimension_id", ""),
                r.get("na", False), r.get("score"), r.get("max_score"),
                r.get("boolean_value"), r.get("choice_value"), r.get("text_value"),
                r.get("notes"), json.dumps(r.get("evidence", [])),
                r.get("weight", 1.0),
            )
            rows.append(_row(row))
    return rows  # type: ignore[return-value]


async def list_criterion_responses(
    pool: asyncpg.Pool,
    result_id: str,
    tenant_id: str,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM evaluation.criterion_responses WHERE result_id=$1 AND tenant_id=$2 ORDER BY created_at ASC",
            result_id, tenant_id,
        )
    return _rows(rows)


# ─── Contestations ────────────────────────────────────────────────────────────

async def create_contestation(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    result_id: str,
    instance_id: str,
    session_id: str,
    contested_by: str,
    contestation_reason: str = "",
) -> dict[str, Any]:
    contest_id = _new_id("evcontest_")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evaluation.contestations
                (id, tenant_id, result_id, instance_id, session_id, contested_by, contestation_reason)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            RETURNING *
            """,
            contest_id, tenant_id, result_id, instance_id, session_id, contested_by, contestation_reason,
        )
        # update result and instance status
        await conn.execute(
            "UPDATE evaluation.results SET eval_status='contested', contested_by=$1, contested_at=now(), contestation_reason=$2, updated_at=now() WHERE id=$3",
            contested_by, contestation_reason, result_id,
        )
        await conn.execute(
            "UPDATE evaluation.instances SET status='contested', updated_at=now() WHERE id=$1",
            instance_id,
        )
    return _row(row)  # type: ignore[return-value]


async def adjudicate_contestation(
    pool: asyncpg.Pool,
    contest_id: str,
    tenant_id: str,
    *,
    status: str,
    adjudicated_by: str,
    adjudication_notes: str = "",
    adjusted_score: float | None = None,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE evaluation.contestations
               SET status=$1, adjudicated_by=$2, adjudicated_at=now(),
                   adjudication_notes=$3, adjusted_score=$4, updated_at=now()
             WHERE id=$5 AND tenant_id=$6
            RETURNING *
            """,
            status, adjudicated_by, adjudication_notes, adjusted_score, contest_id, tenant_id,
        )
    return _row(row)


async def list_contestations(
    pool: asyncpg.Pool,
    tenant_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cond = "WHERE tenant_id=$1"
    args: list[Any] = [tenant_id]
    if status:
        args.append(status)
        cond += f" AND status=${len(args)}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM evaluation.contestations {cond} ORDER BY contested_at DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}",
            *args, limit, offset,
        )
    return _rows(rows)


# ─── Workflow state helpers ────────────────────────────────────────────────────

async def update_result_workflow_state(
    pool: asyncpg.Pool,
    result_id: str,
    *,
    action_required: str | None,
    current_round: int | None = None,
    deadline_at: datetime | None = None,
    resume_token: str | None = None,
    workflow_instance_id: str | None = None,
    locked: bool = False,
    lock_reason: str | None = None,
) -> dict[str, Any] | None:
    """
    Called by the workflow.events Kafka consumer to sync result workflow state.
    - workflow.suspended → action_required set, current_round/deadline_at/resume_token updated
    - workflow.completed → locked=True, action_required=None, resume_token=None
    """
    set_parts = ["action_required=$1", "updated_at=now()"]
    args: list[Any] = [action_required]
    idx = 2

    if current_round is not None:
        set_parts.append(f"current_round=${idx}")
        args.append(current_round)
        idx += 1
    if deadline_at is not None:
        set_parts.append(f"deadline_at=${idx}")
        args.append(deadline_at)
        idx += 1
    if resume_token is not None:
        set_parts.append(f"resume_token=${idx}")
        args.append(resume_token)
        idx += 1
    if workflow_instance_id is not None:
        set_parts.append(f"workflow_instance_id=${idx}")
        args.append(workflow_instance_id)
        idx += 1
    if locked:
        set_parts.append("eval_status='locked'")
        set_parts.append("locked_at=now()")
        set_parts.append("resume_token=NULL")
        if lock_reason:
            set_parts.append(f"lock_reason=${idx}")
            args.append(lock_reason)
            idx += 1

    args.append(result_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE evaluation.results SET {', '.join(set_parts)} "
            f"WHERE id=${idx} RETURNING *",
            *args,
        )
    return _row(row)


async def lock_result(
    pool: asyncpg.Pool,
    result_id: str,
    *,
    lock_reason: str = "manual",
    locked_by: str = "system",
) -> dict[str, Any] | None:
    """
    Permanently lock a result. Called by:
    - evaluation_lock MCP tool (from congelar_resultado workflow step)
    - workflow.events consumer on workflow.completed with lock_reason
    Once locked, eval_status='locked' is irreversible — any further write returns None.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE evaluation.results
               SET eval_status    = 'locked',
                   locked_at      = now(),
                   locked_by      = $1,
                   lock_reason    = $2,
                   action_required = NULL,
                   resume_token   = NULL,
                   updated_at     = now()
             WHERE id = $3
               AND eval_status != 'locked'
            RETURNING *
            """,
            locked_by, lock_reason, result_id,
        )
    return _row(row)


# ─── Pool factory ─────────────────────────────────────────────────────────────

async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
