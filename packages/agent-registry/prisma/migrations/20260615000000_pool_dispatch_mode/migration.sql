-- Frente 1 — Dispatch pull
-- Adds dispatch_mode to the pools table.
--
-- dispatch_mode: "push" (auto-allocate, default — current behaviour) |
--   "pull" (the contact is parked in the pool queue and NOT auto-allocated;
--   a logged-in agent lists and claims it explicitly). Default 'push' keeps
--   every existing pool unchanged.

ALTER TABLE "pools"
  ADD COLUMN "dispatch_mode" TEXT NOT NULL DEFAULT 'push';
