-- Migration: add channel_endpoints table
-- ChannelEndpoint — maps external entry points (webchat slug, WhatsApp number,
-- voice DID, etc.) to service pools.  One record per entry point per tenant.
-- See: docs/arcos/ — Channel Endpoints Layer 1 (2026-05-07)

CREATE TABLE "channel_endpoints" (
    "id"           TEXT         NOT NULL DEFAULT gen_random_uuid()::text,
    "tenant_id"    TEXT         NOT NULL,
    "channel"      TEXT         NOT NULL,   -- webchat | whatsapp | voice | sms | email | webhook
    "identifier"   TEXT         NOT NULL,   -- channel-specific address / slug
    "pool_id"      TEXT         NOT NULL,   -- logical pool_id (soft reference)
    "display_name" TEXT         NOT NULL,
    "settings"     JSONB        NOT NULL DEFAULT '{}',
    "active"       BOOLEAN      NOT NULL DEFAULT true,
    "created_at"   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    "updated_at"   TIMESTAMPTZ  NOT NULL DEFAULT now(),

    CONSTRAINT "channel_endpoints_pkey" PRIMARY KEY ("id")
);

-- Uniqueness: one endpoint per (tenant, channel, identifier)
CREATE UNIQUE INDEX "channel_endpoints_tenant_channel_identifier_key"
  ON "channel_endpoints" ("tenant_id", "channel", "identifier");

CREATE INDEX "channel_endpoints_tenant_id_idx"
  ON "channel_endpoints" ("tenant_id");

CREATE INDEX "channel_endpoints_tenant_id_channel_idx"
  ON "channel_endpoints" ("tenant_id", "channel");

CREATE INDEX "channel_endpoints_tenant_id_channel_active_idx"
  ON "channel_endpoints" ("tenant_id", "channel", "active");
