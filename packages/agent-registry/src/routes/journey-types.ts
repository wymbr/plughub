/**
 * routes/journey-types.ts
 * Arc 17 — CRUD for JourneyType platform definitions.
 *
 * JourneyTypes are tenant-scoped definitions of business process types.
 * Each definition carries a journey_type_id (slug) and sla_ms.
 * Pool config references these via authorized_journey_types[].
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma }   from "../db"
import { z, ZodError } from "zod"

export const journeyTypesRouter = Router()

// ── Inline validators (no separate file needed — schema is small) ─────────────

const CreateJourneyTypeBodySchema = z.object({
  journey_type_id: z.string().regex(/^[a-z0-9_]+$/, "journey_type_id must be snake_case"),
  sla_ms:          z.number().int().positive().optional(),
  description:     z.string().optional(),
})

const UpdateJourneyTypeBodySchema = z.object({
  sla_ms:      z.number().int().positive().optional(),
  description: z.string().optional(),
})

// ── Helpers ───────────────────────────────────────────────────────────────────

function getTenantId(req: Request): string {
  const tid = req.headers["x-tenant-id"] as string | undefined
  if (!tid) throw Object.assign(new Error("x-tenant-id header required"), { statusCode: 400 })
  return tid
}

function formatJourneyType(jt: {
  id: string
  journey_type_id: string
  tenant_id: string
  sla_ms: number | null
  description: string | null
  created_at: Date
  updated_at: Date
}) {
  return {
    id:              jt.id,
    journey_type_id: jt.journey_type_id,
    tenant_id:       jt.tenant_id,
    sla_ms:          jt.sla_ms,
    description:     jt.description,
    created_at:      jt.created_at.toISOString(),
    updated_at:      jt.updated_at.toISOString(),
  }
}

// ── GET /v1/journey-types ─────────────────────────────────────────────────────

journeyTypesRouter.get("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = getTenantId(req)
    const types = await prisma.journeyType.findMany({
      where: { tenant_id: tenantId },
      orderBy: { journey_type_id: "asc" },
    })
    res.json(types.map(formatJourneyType))
  } catch (err) {
    next(err)
  }
})

// ── GET /v1/journey-types/:journey_type_id ────────────────────────────────────

journeyTypesRouter.get("/:journey_type_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = getTenantId(req)
    const journey_type_id = req.params["journey_type_id"] as string
    const jt = await prisma.journeyType.findUnique({
      where: { journey_type_id_tenant_id: { journey_type_id, tenant_id: tenantId } },
    })
    if (!jt) return res.status(404).json({ error: "journey_type not found" })
    res.json(formatJourneyType(jt))
  } catch (err) {
    next(err)
  }
})

// ── POST /v1/journey-types ────────────────────────────────────────────────────

journeyTypesRouter.post("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = getTenantId(req)
    const body = CreateJourneyTypeBodySchema.parse(req.body)

    const existing = await prisma.journeyType.findUnique({
      where: { journey_type_id_tenant_id: { journey_type_id: body.journey_type_id, tenant_id: tenantId } },
    })
    if (existing) {
      return res.status(409).json({ error: "journey_type_id already registered for this tenant" })
    }

    const jt = await prisma.journeyType.create({
      data: {
        journey_type_id: body.journey_type_id,
        tenant_id:       tenantId,
        sla_ms:          body.sla_ms ?? null,
        description:     body.description ?? null,
      },
    })

    res.status(201).json(formatJourneyType(jt))
  } catch (err) {
    next(err)
  }
})

// ── PATCH /v1/journey-types/:journey_type_id ──────────────────────────────────

journeyTypesRouter.patch("/:journey_type_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = getTenantId(req)
    const journey_type_id = req.params["journey_type_id"] as string
    const body = UpdateJourneyTypeBodySchema.parse(req.body)

    const existing = await prisma.journeyType.findUnique({
      where: { journey_type_id_tenant_id: { journey_type_id, tenant_id: tenantId } },
    })
    if (!existing) return res.status(404).json({ error: "journey_type not found" })

    const updated = await prisma.journeyType.update({
      where: { journey_type_id_tenant_id: { journey_type_id, tenant_id: tenantId } },
      data: {
        ...(body.sla_ms      !== undefined && { sla_ms:      body.sla_ms }),
        ...(body.description !== undefined && { description: body.description }),
      },
    })

    res.json(formatJourneyType(updated))
  } catch (err) {
    next(err)
  }
})

// ── DELETE /v1/journey-types/:journey_type_id ─────────────────────────────────

journeyTypesRouter.delete("/:journey_type_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = getTenantId(req)
    const journey_type_id = req.params["journey_type_id"] as string

    const existing = await prisma.journeyType.findUnique({
      where: { journey_type_id_tenant_id: { journey_type_id, tenant_id: tenantId } },
    })
    if (!existing) return res.status(404).json({ error: "journey_type not found" })

    // Guard: check if any pools reference this journey_type_id
    const poolsReferencing = await prisma.pool.findMany({
      where: {
        tenant_id:                tenantId,
        authorized_journey_types: { has: journey_type_id },
      },
      select: { pool_id: true },
    })
    if (poolsReferencing.length > 0) {
      return res.status(409).json({
        error:        "journey_type_in_use",
        detail:       `Cannot delete — referenced by pools: ${poolsReferencing.map(p => p.pool_id).join(", ")}`,
        pool_ids:     poolsReferencing.map(p => p.pool_id),
      })
    }

    await prisma.journeyType.delete({
      where: { journey_type_id_tenant_id: { journey_type_id, tenant_id: tenantId } },
    })

    res.status(204).send()
  } catch (err) {
    next(err)
  }
})
