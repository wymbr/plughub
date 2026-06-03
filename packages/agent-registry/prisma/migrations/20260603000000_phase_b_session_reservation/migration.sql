-- Fase B (queue-attended-model): hybrid session admission.
-- session_reservation: dedicated concurrent-session slots for the pool
-- (cap AND guarantee), carved out of the installation's max_session_total.
-- NULL = pool draws from the shared bucket (total − Σ reservations).
ALTER TABLE "pools"
  ADD COLUMN "session_reservation" INTEGER;
