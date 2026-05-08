-- Migration: add_skill_version_slots
-- 3-slot deploy lifecycle model: previous / current / next
-- Spec: Task #31 — operator promotes (next→current, current→previous)
--       or rolls back (previous→current) with a single button click.

-- ── 1. Create SkillSlot enum ────────────────────────────────────────────────

DO $$ BEGIN
  CREATE TYPE "SkillSlot" AS ENUM ('previous', 'current', 'next');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── 2. Create skill_version_slots table ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS skill_version_slots (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id      TEXT        NOT NULL,
    tenant_id     TEXT        NOT NULL,
    slot          "SkillSlot" NOT NULL,
    yaml_snapshot JSONB,
    config_json   JSONB       NOT NULL DEFAULT '{}',
    pool_ids      TEXT[]      NOT NULL DEFAULT '{}',
    set_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    set_by        TEXT        NOT NULL,

    CONSTRAINT fk_skill_version_slot
        FOREIGN KEY (skill_id, tenant_id)
        REFERENCES skills (skill_id, tenant_id)
        ON DELETE CASCADE,
    CONSTRAINT uq_skill_version_slot
        UNIQUE (skill_id, tenant_id, slot)
);

CREATE INDEX IF NOT EXISTS skill_version_slots_skill_tenant_idx
    ON skill_version_slots (skill_id, tenant_id);
