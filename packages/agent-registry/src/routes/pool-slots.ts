/**
 * routes/pool-slots.ts
 * Pool-centric 3-slot deploy lifecycle.
 * Spec: Task #31 revised
 *
 * Rules:
 *   - Only "next" slot is writable — current/previous are immutable snapshots.
 *   - Promote: next→current, current→previous, next cleared.
 *   - Rollback: previous→current, previous cleared.
 *   - yaml_snapshot is automatically fetched from the skill when not provided.
 *
 * Endpoints (mounted at /v1/pools/:pool_id):
 *   GET  /slots          — return all 3 slots for the pool
 *   PUT  /slots/next     — developer sets the "next" slot (skill_id + config_json)
 *   POST /promote        — operator promotes: next→current, current→previous
 *   POST /rollback       — operator rolls back: previous→current
 */

import { Router, Request, Response, NextFunction } from "express"
import { authorOf, requireAbacWrite } from "../middleware/require-resource-write"
import { prisma, Prisma } from "../db"
import { publishRegistryChanged } from "../infra/kafka"
import { deployViolation, slotDeclared } from "../lib/capacity"
import { judgeSlotCandidate } from "../lib/slot-candidate"
import { promoteSlotsInTx, recordSkillDeployment } from "../lib/slot-promotion"

export const poolSlotsRouter = Router({ mergeParams: true })

// PID-07 — o portão do DEPLOY é o campo da TELA de Deploy (`skill_flows.operacao`),
// declarado ROTA A ROTA. Até aqui estas rotas só tinham portão por ACIDENTE: o
// `app.use("/v1/pools", requireResourceWrite, …)` casa por prefixo e cobria
// `/v1/pools/:id/slots|promote|rollback` com `config.resources` (preset admin-only),
// enquanto o comentário do `app.ts` dizia "não gateado". Consequência medida: o
// devops via a tela de Deploy e tomava 403 em set-next e promote.
// Por rota, e não num `router.use`: este router é montado em `/v1/pools/:pool_id`, e
// um `use` aqui também pegaria o `PUT /v1/pools/:id` do pool — que é da tela Recursos.
export const requireDeployWrite = requireAbacWrite("skill_flows", "operacao")

// ── Helpers ────────────────────────────────────────────────────────────────────

export function _getTenantId(req: Request): string {
  return (req.headers["x-tenant-id"] as string) ?? "tenant_default"
}

export function _formatSlot(row: Record<string, unknown> | null, slot: string) {
  if (!row) return { slot, set: false }
  const { id: _id, ...rest } = row
  return { ...rest, slot, set: true }
}

/** Fetch skill flow snapshot from the registry (used when developer omits yaml_snapshot).
 *
 *  UMA definição, sem rascunho (2026-07-13): o set-next congela a DEFINIÇÃO atual
 *  (`flow`) — a mesma que o editor grava e cujo `updated_at` a UI compara com o
 *  `set_at` do slot para mostrar "há alterações não implantadas".
 *
 *  O `flow_draft` foi eliminado (existia só para impedir vazamento à produção, o que
 *  hoje o snapshot de slot já garante — o bridge não roda mais definição viva). Fica
 *  o fallback de leitura para linhas antigas que ainda tenham um draft pendente. */
export async function _fetchSkillSnapshot(skillId: string, tenantId: string): Promise<unknown | null> {
  try {
    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    const rec = skill as unknown as Record<string, unknown> | null
    return (rec?.["flow"] ?? rec?.["flow_draft"]) ?? null
  } catch {
    return null
  }
}

// ── GET /v1/pools/:pool_id/slots ───────────────────────────────────────────────

poolSlotsRouter.get("/slots", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const poolId   = req.params["pool_id"]!

    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    const rows = await (prisma as any).poolSkillSlot.findMany({
      where: { pool_id: poolId, tenant_id: tenantId },
    }) as Record<string, unknown>[]

    const bySlot = Object.fromEntries(rows.map(r => [r["slot"], r]))

    return res.json({
      pool_id: poolId,
      slots: {
        previous: _formatSlot(bySlot["previous"] ?? null, "previous"),
        current:  _formatSlot(bySlot["current"]  ?? null, "current"),
        next:     _formatSlot(bySlot["next"]      ?? null, "next"),
      },
    })
  } catch (err) {
    return next(err)
  }
})

// ── PUT /v1/pools/:pool_id/slots/next ─────────────────────────────────────────
// Only "next" is writable. Returns 403 for previous/current.

poolSlotsRouter.put("/slots/:slot", requireDeployWrite, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const userId   = authorOf(req)
    const poolId   = req.params["pool_id"]!
    const slot     = req.params["slot"]

    if (slot !== "next") {
      return res.status(403).json({
        error: `Slot '${slot}' é imutável. Apenas o slot 'next' pode ser editado.`,
      })
    }

    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    const { skill_id, config_json, yaml_snapshot } = req.body as {
      skill_id?:     string
      config_json?:  Record<string, unknown>
      yaml_snapshot?: unknown
    }

    if (!skill_id) {
      return res.status(400).json({ error: "skill_id é obrigatório para configurar o slot next" })
    }

    // Verify skill exists
    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skill_id, tenant_id: tenantId } },
    })
    if (!skill) return res.status(404).json({ error: `Skill '${skill_id}' não encontrada` })

    // Capacity-governance item 3b: Σ declarada nos deploys ≤ C.
    // Feedback cedo, na declaração (re-checada no promote — C pode mudar entre
    // os dois). Reduções/iguais sempre passam (re-sync idempotente do
    // RegistrySyncer não quebra); sem C → fail-open.
    // (Pool humano é recusado antes, pela casa dos portões: não se soma a capacidade
    // de um deploy que não pode existir.)
    if ((pool as { agent_kind?: string | null }).agent_kind !== "human") {
      const violation = await deployViolation(tenantId, poolId, slotDeclared(config_json))
      if (violation) return res.status(422).json(violation)
    }

    // Auto-fetch yaml_snapshot if not provided
    const snapshot = yaml_snapshot != null
      ? yaml_snapshot
      : await _fetchSkillSnapshot(skill_id, tenantId)

    // PID-16 — os portões do candidato moram em `lib/slot-candidate.ts` (pool humano,
    // snapshot ausente, masked × canais, perfil × steps, config obrigatória, piso de
    // identidade). Feedback cedo aqui; re-julgados no promote porque o pool, o skill e
    // a config podem mudar entre a declaração e a promoção.
    const veredito = judgeSlotCandidate({
      pool:       pool as unknown as Record<string, unknown>,
      skill:      skill as unknown as Record<string, unknown>,
      snapshot,
      configJson: config_json ?? {},
      poolId,
      skillId:    skill_id,
      stage:      "set-next",
    })
    if (veredito.kind === "block") {
      return res.status(422).json({ error: veredito.error, ...(veredito.message ? { message: veredito.message } : {}) })
    }

    const row = await (prisma as any).poolSkillSlot.upsert({
      where:  { pool_id_tenant_id_slot: { pool_id: poolId, tenant_id: tenantId, slot: "next" } },
      update: {
        skill_id,
        config_json:   config_json ?? {},
        yaml_snapshot: snapshot != null ? (snapshot as Prisma.InputJsonValue) : Prisma.DbNull,
        set_at:        new Date(),
        set_by:        userId,
      },
      create: {
        pool_id:       poolId,
        tenant_id:     tenantId,
        slot:          "next",
        skill_id,
        config_json:   config_json ?? {},
        yaml_snapshot: snapshot != null ? (snapshot as Prisma.InputJsonValue) : Prisma.DbNull,
        set_by:        userId,
      },
    }) as Record<string, unknown>

    // O aviso viaja no CORPO e no log. Duas casas de propósito: a UI pode não
    // renderizar o corpo, e o log pode não ser lido — se depender de uma só, o
    // fato some, que é como a MSK-01 sobreviveu.
    return res.json({
      ..._formatSlot(row, "next"),
      ...(veredito.warnings.length ? { warnings: veredito.warnings } : {}),
    })
  } catch (err) {
    return next(err)
  }
})

// ── POST /v1/pools/:pool_id/promote ───────────────────────────────────────────
// next → current, current → previous, next cleared.

poolSlotsRouter.post("/promote", requireDeployWrite, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const userId   = authorOf(req)
    const poolId   = req.params["pool_id"]!

    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    const rows = await (prisma as any).poolSkillSlot.findMany({
      where: { pool_id: poolId, tenant_id: tenantId },
    }) as Record<string, unknown>[]

    const bySlot      = Object.fromEntries(rows.map((r: Record<string, unknown>) => [r["slot"], r]))
    const nextSlot    = bySlot["next"]    as Record<string, unknown> | undefined
    const currentSlot = bySlot["current"] as Record<string, unknown> | undefined

    if (!nextSlot) {
      return res.status(409).json({ error: "Slot 'next' não está configurado — configure antes de promover" })
    }

    // Capacity-governance item 3b: o promote é o momento em que a declaração
    // vira efetiva (next → current) — revalida contra o C vigente.
    // Rollback fica ISENTO (operação de emergência nunca bloqueia).
    const violation = await deployViolation(
      tenantId, poolId, slotDeclared(nextSlot["config_json"]),
    )
    if (violation) return res.status(422).json(violation)

    // Re-julga o candidato. NÃO é redundante com o set-next: `Pool.channel_types` pode
    // ter perdido o canal capaz, e o `config_params` do skill ganhado um obrigatório,
    // entre uma coisa e outra — e o promote é o instante em que o snapshot passa a
    // atender contato de verdade. O ROLLBACK fica isento: emergência nunca bloqueia.
    const skillProm = (nextSlot["skill_id"] as string) || ""
    const skillRow = skillProm
      ? await prisma.skill.findUnique({
          where: { skill_id_tenant_id: { skill_id: skillProm, tenant_id: tenantId } },
        })
      : null
    const veredito = judgeSlotCandidate({
      pool:       pool as unknown as Record<string, unknown>,
      skill:      skillRow as unknown as Record<string, unknown> | null,
      snapshot:   nextSlot["yaml_snapshot"],
      configJson: nextSlot["config_json"],
      poolId,
      skillId:    skillProm,
      stage:      "promote",
    })
    if (veredito.kind === "block") {
      return res.status(422).json({ error: veredito.error, ...(veredito.message ? { message: veredito.message } : {}) })
    }

    const now = new Date()

    await prisma.$transaction(async (tx: any) => {
      await promoteSlotsInTx(tx, {
        tenantId, poolId, userId, now,
        candidate: nextSlot as unknown as { skill_id: string | null; config_json: unknown; yaml_snapshot: unknown },
        current:   currentSlot as unknown as { skill_id: string | null; config_json: unknown; yaml_snapshot: unknown } | undefined,
      })
    })

    await recordSkillDeployment({
      tenantId, poolId, userId, now,
      skillId:  skillProm,
      snapshot: nextSlot["yaml_snapshot"],
      notes:    "promote",
    })

    await publishRegistryChanged(tenantId, "pool", poolId, "updated")

    const updated = await (prisma as any).poolSkillSlot.findMany({
      where: { pool_id: poolId, tenant_id: tenantId },
    }) as Record<string, unknown>[]
    const updatedBySlot = Object.fromEntries(updated.map((r: Record<string, unknown>) => [r["slot"], r]))

    return res.json({
      pool_id: poolId,
      action:  "promoted",
      slots: {
        previous: _formatSlot(updatedBySlot["previous"] ?? null, "previous"),
        current:  _formatSlot(updatedBySlot["current"]  ?? null, "current"),
        next:     _formatSlot(updatedBySlot["next"]      ?? null, "next"),
      },
    })
  } catch (err) {
    return next(err)
  }
})

// ── POST /v1/pools/:pool_id/rollback ──────────────────────────────────────────
// previous → current, previous cleared. Does NOT touch "next".

poolSlotsRouter.post("/rollback", requireDeployWrite, async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const userId   = authorOf(req)
    const poolId   = req.params["pool_id"]!

    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    const rows = await (prisma as any).poolSkillSlot.findMany({
      where: { pool_id: poolId, tenant_id: tenantId },
    }) as Record<string, unknown>[]

    const bySlot       = Object.fromEntries(rows.map((r: Record<string, unknown>) => [r["slot"], r]))
    const previousSlot = bySlot["previous"] as Record<string, unknown> | undefined

    if (!previousSlot) {
      return res.status(409).json({ error: "Slot 'previous' não está configurado — nada para fazer rollback" })
    }

    const now = new Date()

    await prisma.$transaction(async (tx: any) => {
      // previous → current
      await tx.poolSkillSlot.upsert({
        where:  { pool_id_tenant_id_slot: { pool_id: poolId, tenant_id: tenantId, slot: "current" } },
        update: {
          skill_id:      previousSlot["skill_id"] ?? null,
          config_json:   previousSlot["config_json"] ?? {},
          yaml_snapshot: (previousSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
          set_at:        now,
          set_by:        userId,
        },
        create: {
          pool_id:       poolId,
          tenant_id:     tenantId,
          slot:          "current",
          skill_id:      previousSlot["skill_id"] as string ?? null,
          config_json:   previousSlot["config_json"] ?? {},
          yaml_snapshot: (previousSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
          set_by:        userId,
        },
      })

      // clear previous
      await tx.poolSkillSlot.deleteMany({
        where: { pool_id: poolId, tenant_id: tenantId, slot: "previous" },
      })
    })

    await publishRegistryChanged(tenantId, "pool", poolId, "updated")

    const updated = await (prisma as any).poolSkillSlot.findMany({
      where: { pool_id: poolId, tenant_id: tenantId },
    }) as Record<string, unknown>[]
    const updatedBySlot = Object.fromEntries(updated.map((r: Record<string, unknown>) => [r["slot"], r]))

    return res.json({
      pool_id: poolId,
      action:  "rolled_back",
      slots: {
        previous: _formatSlot(updatedBySlot["previous"] ?? null, "previous"),
        current:  _formatSlot(updatedBySlot["current"]  ?? null, "current"),
        next:     _formatSlot(updatedBySlot["next"]      ?? null, "next"),
      },
    })
  } catch (err) {
    return next(err)
  }
})
