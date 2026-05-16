-- Migration: add max_reply_time_ms to pools
-- Maximum reply time per customer message (ms). Optional — no limit when NULL.

ALTER TABLE "pools" ADD COLUMN "max_reply_time_ms" INTEGER;
