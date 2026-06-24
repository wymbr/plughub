/**
 * routes/skill-slots.ts — APOSENTADO (Skill Versioning Fase E, 2026-06-24).
 *
 * O ciclo de 3 slots POR SKILL (SkillVersionSlot, Task #31) foi removido por ser
 * duplicação do PoolSkillSlot (autoritativo, por-pool). O deploy é pool-centric:
 * PoolSkillSlot (`/v1/pools/:id/slots` + promote/rollback) + SkillDeployment
 * (append-log). Este router NÃO é montado em app.ts; mantido como stub vazio
 * apenas para não quebrar imports legados.
 */
import { Router } from "express"

export const skillSlotsRouter = Router()
