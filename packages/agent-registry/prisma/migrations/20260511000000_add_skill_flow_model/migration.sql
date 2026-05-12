-- Migration: 20260511000000_add_skill_flow_model
--
-- Adds flow_model column to skills table.
-- Computed at PUT/upsert time by inspecting the flow.steps array for
-- suspend or collect step types, which require the workflow-api machinery.
-- Default 'agent' — retroactively backfilled from stored flow JSON.

ALTER TABLE skills
  ADD COLUMN flow_model TEXT NOT NULL DEFAULT 'agent';

-- Backfill: mark existing skills as 'workflow' if their flow JSON
-- contains at least one step with type 'suspend' or 'collect'.
UPDATE skills
SET flow_model = 'workflow'
WHERE flow IS NOT NULL
  AND flow::jsonb ? 'steps'
  AND EXISTS (
    SELECT 1
    FROM jsonb_array_elements(flow::jsonb -> 'steps') AS step
    WHERE step ->> 'type' IN ('suspend', 'collect')
  );
