-- Migration: add inbound_journey_resume to pools
-- Arc 16 Phase E: opt-in flag that signals a pool's AI agent skill
-- should call journey_check_pending(customer_id) at inbound session start
-- to detect and offer resumption of multi-session service journeys.
--
-- Default false — explicit opt-in required per pool.

ALTER TABLE "pools"
ADD COLUMN IF NOT EXISTS "inbound_journey_resume" BOOLEAN NOT NULL DEFAULT false;
