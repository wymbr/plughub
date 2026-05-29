-- Arc 19 — Webhook pool fields
-- Adds webhook_skill_id and max_concurrent_sessions to the pools table.
--
-- webhook_skill_id: the skill endpoint for webhook pools (the "DIN" of the
--   webhook channel). Required when channel_types includes 'webhook'.
--   Determines the trigger URL: POST /v1/channels/webhook/{skill_id}
--
-- max_concurrent_sessions: capacity limit for webhook pools (replaces
--   agent-login-based capacity). Informational for human/AI pools.

ALTER TABLE "pools"
  ADD COLUMN "webhook_skill_id"        TEXT,
  ADD COLUMN "max_concurrent_sessions" INTEGER;
