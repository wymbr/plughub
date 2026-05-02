-- Migration: add hooks column to pools table
ALTER TABLE pools ADD COLUMN IF NOT EXISTS hooks JSONB;
