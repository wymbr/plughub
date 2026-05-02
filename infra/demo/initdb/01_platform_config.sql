-- 01_platform_config.sql
-- Creates the platform_config table in the plughub_demo database.
-- This script is run automatically by the postgres Docker image on first start
-- (when the data volume is empty / after docker compose down -v).
-- The script runs as the POSTGRES_USER (plughub, a superuser) against
-- the POSTGRES_DB (plughub_demo), so no explicit GRANTs are needed.

CREATE TABLE IF NOT EXISTS public.platform_config (
    id          SERIAL      PRIMARY KEY,
    tenant_id   TEXT        NOT NULL DEFAULT '__global__',
    namespace   TEXT        NOT NULL,
    key         TEXT        NOT NULL,
    value       JSONB       NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_platform_config UNIQUE (tenant_id, namespace, key)
);

CREATE INDEX IF NOT EXISTS idx_platform_config_lookup
    ON public.platform_config (tenant_id, namespace, key);
