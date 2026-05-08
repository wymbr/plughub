-- Migration: add gateway_config_id to channel_endpoints
-- Nullable FK — existing rows default to NULL (backward compatible)

ALTER TABLE "channel_endpoints"
  ADD COLUMN "gateway_config_id" TEXT;

-- Index for fast lookup of endpoints belonging to a GatewayConfig
CREATE INDEX "channel_endpoints_gateway_config_id_idx"
  ON "channel_endpoints" ("gateway_config_id");
