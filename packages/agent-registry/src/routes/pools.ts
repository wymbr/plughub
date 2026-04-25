/**
 * routes/pools.ts
 * CRUD de pools — spec 4.5
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma, Prisma }    from "../db"
import { CreatePoolSchema, UpdatePoolSchema } from "../validators/pool"
import { ZodError }          from "zod"
import { publishRegistryEvent } from "../infra/kafka"

export const poolsRouter = Router()

// ─────────────────────────────────────────────
// POST /v1/pools
// ─────────────────────────────────────────────
poolsRouter.post("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId  = _getTenantId(req)
    const createdBy = _getUserId(req)
    const body      = CreatePoolSchema.parse(req.body)

    // Verificar duplicata
    const existing = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: body.pool_id, tenant_id: tenantId } },
    })
    if (existing) {
      return res.status(409).json({ error: "pool_id já registrado neste tenant" })
    }

    // Validar evaluation_template_id se fornecido
    // TODO: consultar tabela evaluation_templates

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const pool = await prisma.pool.create({
      data: {
        pool_id:               body.pool_id,
        tenant_id:             tenantId,
        description:           body.description ?? null,
        channel_types:         body.channel_types,
        sla_target_ms:         body.sla_target_ms,
        routing_expression:    body.routing_expression ?? Prisma.DbNull,
        evaluation_template_id: body.evaluation_template_id ?? null,
        supervisor_config:     body.supervisor_config ?? Prisma.DbNull,
        queue_config:          body.queue_config ?? Prisma.DbNull,
        created_by:            createdBy,
      } as any,
    })

    const formatted = _formatPool(pool)

    // Publica evento para o Routing Engine atualizar o cache Redis (pool_config)
    await publishRegistryEvent({
      event:     "pool.registered",
      tenant_id: tenantId,
      pool:      formatted,
    })

    return res.status(201).json(formatted)
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/pools
// ─────────────────────────────────────────────
poolsRouter.get("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const status   = (req.query["status"] as string) ?? "active"

    const pools = await prisma.pool.findMany({
      where:   { tenant_id: tenantId, status: status as never },
      orderBy: { created_at: "asc" },
    })

    return res.json({ pools: pools.map(_formatPool), total: pools.length })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/pools/:pool_id
// ─────────────────────────────────────────────
poolsRouter.get("/:pool_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const pool     = await prisma.pool.findUnique({
      where:   { pool_id_tenant_id: { pool_id: req.params["pool_id"]!, tenant_id: tenantId } },
      include: { agent_types: { include: { agent_type: true } } },
    })

    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })
    return res.json(_formatPool(pool))
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// PUT /v1/pools/:pool_id
// ─────────────────────────────────────────────
poolsRouter.put("/:pool_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const body     = UpdatePoolSchema.parse(req.body)

    const existing = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: req.params["pool_id"]!, tenant_id: tenantId } },
    })
    if (!existing) return res.status(404).json({ error: "Pool não encontrado" })

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const updated = await prisma.pool.update({
      where: { id: existing.id },
      data: {
        ...(body.description           !== undefined && { description:           body.description }),
        ...(body.channel_types         !== undefined && { channel_types:         body.channel_types }),
        ...(body.sla_target_ms         !== undefined && { sla_target_ms:         body.sla_target_ms }),
        ...(body.routing_expression    !== undefined && { routing_expression:    body.routing_expression }),
        ...(body.evaluation_template_id !== undefined && { evaluation_template_id: body.evaluation_template_id }),
        ...(body.supervisor_config     !== undefined && { supervisor_config:     body.supervisor_config }),
        ...(body.queue_config          !== undefined && { queue_config:          body.queue_config }),
      } as any,
    })

    const formatted = _formatPool(updated)

    // Publica evento de atualização para o Routing Engine invalidar/atualizar cache
    await publishRegistryEvent({
      event:     "pool.updated",
      tenant_id: tenantId,
      pool:      formatted,
    })

    return res.json(formatted)
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────
function _getTenantId(req: Request): string {
  // Em produção: extraído do JWT via middleware de autenticação
  return (req.headers["x-tenant-id"] as string) ?? "tenant_default"
}

function _getUserId(req: Request): string {
  return (req.headers["x-user-id"] as string) ?? "system"
}

function _formatPool(pool: Record<string, unknown>): Record<string, unknown> {
  const { id: _id, ...rest } = pool
  return rest
}
