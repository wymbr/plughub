-- Migration: add context_visibility to pools
-- Stores { operator_namespaces: string[] } JSON for ContextoTab filtering.
-- Null = use default (["service", "journey", "session"]).

ALTER TABLE "pools" ADD COLUMN "context_visibility" JSONB;
