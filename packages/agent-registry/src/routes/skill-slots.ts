/**
 * routes/skill-slots.ts
 * 3-slot deploy lifecycle for skills.
 * Spec: Task #31
 *
 * Slots: previous (anterior / safe rollback) | current (corrente / live) | next (próxima / candidate)
 *
 * Endpoints:
 *   GET  /v1/skills/:skill_id/slots           — list all 3 slots
 *   PUT  /v1/skills/:skill_id/slots/:slot     — developer sets a slot
 *   POST /v1/skills/:skill_id/promote         — operator: next→current, current→previous
 *   POST /v1/skills/:skill_id/rollback        — operator: previous→current
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma, Prisma } from "../db"
import { publishRegistryChanged } from "../infra/kafka"

export const skillSlotsRouter = Router({ mergeParams: true })

// ── Helpers ────────────────────────────────────────────────────────────────────

function _getTenantId(req: Request): string {
  return (req.headers["x-tenant-id"] as string) ?? "tenant_default"
}
function _getUserId(req: Request): string {
  return (req.headers["x-user-id"] as string) ?? "system"
}

const VALID_SLOTS = ["previous", "current", "next"] as const
type SlotName = typeof VALID_SLOTS[number]

function _isValidSlot(s: unknown): s is SlotName {
  return typeof s === "string" && (VALID_SLOTS as readonly string[]).includes(s)
}

function _formatSlot(row: Record<string, unknown> | null, slot: SlotName) {
  if (!row) return { slot, set: false }
  const { id: _id, ...rest } = row
  return { ...rest, slot, set: true }
}

// ── GET /v1/skills/:skill_id/slots ─────────────────────────────────────────────

skillSlotsRouter.get("/slots", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const skillId  = req.params["skill_id"]!

    // Verify skill exists
    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    if (!skill) return res.status(404).json({ error: "Skill não encontrada" })

    const rows = await (prisma as any).skillVersionSlot.findMany({
      where: { skill_id: skillId, tenant_id: tenantId },
    }) as Record<string, unknown>[]

    const bySlot = Object.fromEntries(rows.map(r => [r["slot"], r]))

    return res.json({
      skill_id: skillId,
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

// ── PUT /v1/skills/:skill_id/slots/:slot ───────────────────────────────────────
// Developer sets the content of a slot (yaml_snapshot, config_json, pool_ids).

skillSlotsRouter.put("/slots/:slot", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const userId   = _getUserId(req)
    const skillId  = req.params["skill_id"]!
    const slot     = req.params["slot"]

    if (!_isValidSlot(slot)) {
      return res.status(400).json({ error: "Slot inválido. Use: previous | current | next" })
    }

    // Verify skill exists
    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    if (!skill) return res.status(404).json({ error: "Skill não encontrada" })

    const { yaml_snapshot, config_json, pool_ids } = req.body as {
      yaml_snapshot?: unknown
      config_json?:   Record<string, unknown>
      pool_ids?:      string[]
    }

    const row = await (prisma as any).skillVersionSlot.upsert({
      where:  { skill_id_tenant_id_slot: { skill_id: skillId, tenant_id: tenantId, slot } },
      update: {
        yaml_snapshot: yaml_snapshot != null ? (yaml_snapshot as Prisma.InputJsonValue) : Prisma.DbNull,
        config_json:   config_json ?? {},
        pool_ids:      pool_ids    ?? [],
        set_at:        new Date(),
        set_by:        userId,
      },
      create: {
        skill_id:      skillId,
        tenant_id:     tenantId,
        slot,
        yaml_snapshot: yaml_snapshot != null ? (yaml_snapshot as Prisma.InputJsonValue) : Prisma.DbNull,
        config_json:   config_json ?? {},
        pool_ids:      pool_ids    ?? [],
        set_by:        userId,
      },
    }) as Record<string, unknown>

    return res.json(_formatSlot(row, slot))
  } catch (err) {
    return next(err)
  }
})

// ── POST /v1/skills/:skill_id/promote ─────────────────────────────────────────
// Operator promotes: next → current, current → previous.
// Requires "next" slot to be populated.

skillSlotsRouter.post("/promote", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const userId   = _getUserId(req)
    const skillId  = req.params["skill_id"]!

    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    if (!skill) return res.status(404).json({ error: "Skill não encontrada" })

    const rows = await (prisma as any).skillVersionSlot.findMany({
      where: { skill_id: skillId, tenant_id: tenantId },
    }) as Record<string, unknown>[]

    const bySlot = Object.fromEntries(rows.map((r: Record<string, unknown>) => [r["slot"], r]))
    const nextSlot    = bySlot["next"]    as Record<string, unknown> | undefined
    const currentSlot = bySlot["current"] as Record<string, unknown> | undefined

    if (!nextSlot) {
      return res.status(409).json({ error: "Slot 'next' não está configurado — configure antes de promover" })
    }

    const now = new Date()

    await prisma.$transaction(async (tx: any) => {
      // current → previous (upsert)
      if (currentSlot) {
        await tx.skillVersionSlot.upsert({
          where:  { skill_id_tenant_id_slot: { skill_id: skillId, tenant_id: tenantId, slot: "previous" } },
          update: {
            yaml_snapshot: (currentSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
            config_json:   currentSlot["config_json"] ?? {},
            pool_ids:      currentSlot["pool_ids"] ?? [],
            set_at:        now,
            set_by:        userId,
          },
          create: {
            skill_id:      skillId,
            tenant_id:     tenantId,
            slot:          "previous",
            yaml_snapshot: (currentSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
            config_json:   currentSlot["config_json"] ?? {},
            pool_ids:      currentSlot["pool_ids"] ?? [],
            set_by:        userId,
          },
        })
      }

      // next → current (upsert)
      await tx.skillVersionSlot.upsert({
        where:  { skill_id_tenant_id_slot: { skill_id: skillId, tenant_id: tenantId, slot: "current" } },
        update: {
          yaml_snapshot: (nextSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
          config_json:   nextSlot["config_json"] ?? {},
          pool_ids:      nextSlot["pool_ids"] ?? [],
          set_at:        now,
          set_by:        userId,
        },
        create: {
          skill_id:      skillId,
          tenant_id:     tenantId,
          slot:          "current",
          yaml_snapshot: (nextSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
          config_json:   nextSlot["config_json"] ?? {},
          pool_ids:      nextSlot["pool_ids"] ?? [],
          set_by:        userId,
        },
      })

      // clear next slot
      await tx.skillVersionSlot.deleteMany({
        where: { skill_id: skillId, tenant_id: tenantId, slot: "next" },
      })
    })

    // Trigger skill hot-reload in orchestrator-bridge
    await publishRegistryChanged(tenantId, "skill", skillId, "updated")

    // Return new slot state
    const updated = await (prisma as any).skillVersionSlot.findMany({
      where: { skill_id: skillId, tenant_id: tenantId },
    }) as Record<string, unknown>[]
    const updatedBySlot = Object.fromEntries(updated.map((r: Record<string, unknown>) => [r["slot"], r]))

    return res.json({
      skill_id: skillId,
      action:   "promoted",
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

// ── POST /v1/skills/:skill_id/rollback ────────────────────────────────────────
// Operator rolls back: previous → current.
// Does NOT touch the "next" slot. "current" is replaced (not pushed to "next").

skillSlotsRouter.post("/rollback", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const userId   = _getUserId(req)
    const skillId  = req.params["skill_id"]!

    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    if (!skill) return res.status(404).json({ error: "Skill não encontrada" })

    const rows = await (prisma as any).skillVersionSlot.findMany({
      where: { skill_id: skillId, tenant_id: tenantId },
    }) as Record<string, unknown>[]

    const bySlot = Object.fromEntries(rows.map((r: Record<string, unknown>) => [r["slot"], r]))
    const previousSlot = bySlot["previous"] as Record<string, unknown> | undefined

    if (!previousSlot) {
      return res.status(409).json({ error: "Slot 'previous' não está configurado — nada para fazer rollback" })
    }

    const now = new Date()

    await prisma.$transaction(async (tx: any) => {
      // previous → current (upsert)
      await tx.skillVersionSlot.upsert({
        where:  { skill_id_tenant_id_slot: { skill_id: skillId, tenant_id: tenantId, slot: "current" } },
        update: {
          yaml_snapshot: (previousSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
          config_json:   previousSlot["config_json"] ?? {},
          pool_ids:      previousSlot["pool_ids"] ?? [],
          set_at:        now,
          set_by:        userId,
        },
        create: {
          skill_id:      skillId,
          tenant_id:     tenantId,
          slot:          "current",
          yaml_snapshot: (previousSlot["yaml_snapshot"] ?? Prisma.DbNull) as Prisma.InputJsonValue,
          config_json:   previousSlot["config_json"] ?? {},
          pool_ids:      previousSlot["pool_ids"] ?? [],
          set_by:        userId,
        },
      })

      // clear previous slot (it is now current)
      await tx.skillVersionSlot.deleteMany({
        where: { skill_id: skillId, tenant_id: tenantId, slot: "previous" },
      })
    })

    // Trigger skill hot-reload in orchestrator-bridge
    await publishRegistryChanged(tenantId, "skill", skillId, "updated")

    // Return new slot state
    const updated = await (prisma as any).skillVersionSlot.findMany({
      where: { skill_id: skillId, tenant_id: tenantId },
    }) as Record<string, unknown>[]
    const updatedBySlot = Object.fromEntries(updated.map((r: Record<string, unknown>) => [r["slot"], r]))

    return res.json({
      skill_id: skillId,
      action:   "rolled_back",
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
