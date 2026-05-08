-- Migration: add calendar_id to pools table
-- Stores a display-cache reference to the calendar-api calendar UUID.
-- The authoritative link lives in calendar.calendar_associations.

ALTER TABLE "pools"
ADD COLUMN IF NOT EXISTS "calendar_id" TEXT;
