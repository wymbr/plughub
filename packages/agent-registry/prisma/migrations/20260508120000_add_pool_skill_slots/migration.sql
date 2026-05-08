-- Migration: add_pool_skill_slots
-- Pool-centric 3-slot deploy model (Task #31 revised)
-- Key: pool_id + tenant_id + slot
-- Only "next" is editable; current/previous are immutable snapshots.
-- SkillSlot enum already exists from 20260508000000_add_skill_version_slots.

CREATE TABLE IF NOT EXISTS pool_skill_slots (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    pool_id       TEXT        NOT NULL,
    tenant_id     TEXT        NOT NULL,
    slot          "SkillSlot" NOT NULL,

    skill_id      TEXT,                        -- null = slot empty
    config_json   JSONB       NOT NULL DEFAULT '{}',
    yaml_snapshot JSONB,                        -- flow snapshot at set time

    set_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    set_by        TEXT        NOT NULL,

    CONSTRAINT fk_pool_skill_slot_pool
        FOREIGN KEY (pool_id, tenant_id)
        REFERENCES pools (pool_id, tenant_id)
        ON DELETE CASCADE,

    CONSTRAINT uq_pool_skill_slot
        UNIQUE (pool_id, tenant_id, slot)
);

CREATE INDEX IF NOT EXISTS pool_skill_slots_pool_tenant_idx
    ON pool_skill_slots (pool_id, tenant_id);
