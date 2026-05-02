-- Migration: add_skill_deployments
-- Adds deploy lifecycle tracking to skills and a deployment history table

-- ── 1. Add deploy columns to skills table ──────────────────────────────────

ALTER TABLE skills
  ADD COLUMN IF NOT EXISTS deploy_status TEXT NOT NULL DEFAULT 'draft',
  ADD COLUMN IF NOT EXISTS published_at  TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS skills_tenant_deploy_status_idx
  ON skills (tenant_id, deploy_status);

-- ── 2. Create skill_deployments table ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS skill_deployments (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id      TEXT        NOT NULL,
    tenant_id     TEXT        NOT NULL,
    version       TEXT        NOT NULL,
    pool_ids      TEXT[]      NOT NULL DEFAULT '{}',
    yaml_snapshot JSONB,
    deployed_by   TEXT        NOT NULL,
    deployed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes         TEXT,

    CONSTRAINT fk_skill_deployment
        FOREIGN KEY (skill_id, tenant_id)
        REFERENCES skills (skill_id, tenant_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS skill_deployments_skill_tenant_idx
  ON skill_deployments (skill_id, tenant_id);

CREATE INDEX IF NOT EXISTS skill_deployments_tenant_idx
  ON skill_deployments (tenant_id);
