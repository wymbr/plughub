-- Migration: 20260513000000_add_skill_delegation_input
-- Adds delegation_input column to skills table.
-- delegation_input stores a DelegationSchema (typed fields shown in the
-- DelegarTarefaDrawer in the Agent Assist Console) as a nullable JSON column.
-- When null the drawer falls back to a free-text textarea (current behaviour).

ALTER TABLE "skills" ADD COLUMN "delegation_input" JSONB;
