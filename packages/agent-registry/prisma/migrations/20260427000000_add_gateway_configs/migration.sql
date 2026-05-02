-- Migration: add gateway_configs table
-- GatewayConfig — channel credential management per tenant

CREATE TABLE "gateway_configs" (
    "id"           TEXT         NOT NULL DEFAULT gen_random_uuid()::text,
    "tenant_id"    TEXT         NOT NULL,
    "channel"      TEXT         NOT NULL,
    "display_name" TEXT         NOT NULL,
    "active"       BOOLEAN      NOT NULL DEFAULT true,
    "credentials"  JSONB        NOT NULL DEFAULT '{}',
    "settings"     JSONB        NOT NULL DEFAULT '{}',
    "created_at"   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    "updated_at"   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    "created_by"   TEXT         NOT NULL DEFAULT 'operator',

    CONSTRAINT "gateway_configs_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "gateway_configs_tenant_id_idx"         ON "gateway_configs"("tenant_id");
CREATE INDEX "gateway_configs_tenant_id_channel_idx" ON "gateway_configs"("tenant_id", "channel");
