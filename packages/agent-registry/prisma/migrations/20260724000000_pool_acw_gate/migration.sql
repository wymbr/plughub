-- Camada C (detach de hooks de finalização) — ACW como regra de agent_ready.
-- Adds acw_gate to the pools table.
--
-- acw_gate: "none" (default — não bloqueia; wrap-up é backlog no inbox) |
--   "soft" (atendente segue disponível; supervisor vê pendências) |
--   "hard" (o Routing Engine NÃO roteia novo contato enquanto houver wrap-up
--   detached pendente daquele user_id — agent_ready efetivamente gated).
-- Default 'none' mantém todo pool existente inalterado (o ACW bloqueante clássico
-- do wrap-up INLINE segue via wrap_up_pending, independente deste campo).

ALTER TABLE "pools"
  ADD COLUMN "acw_gate" TEXT NOT NULL DEFAULT 'none';
