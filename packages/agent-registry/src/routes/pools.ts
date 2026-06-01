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
        sla_target_ms:           body.sla_target_ms,
        webhook_skill_id:        body.webhook_skill_id ?? null,
        max_concurrent_sessions: body.max_concurrent_sessions ?? null,
        max_reply_time_ms:       body.max_reply_time_ms ?? null,
        routing_expression:      body.routing_expression ?? Prisma.DbNull,
        evaluation_template_id: body.evaluation_template_id ?? null,
        supervisor_config:     body.supervisor_config ?? Prisma.DbNull,
        queue_config:          body.queue_config ?? Prisma.DbNull,
        mentionable_pools:     body.mentionable_pools ?? Prisma.DbNull,
        agent_groups:          body.agent_groups ?? [],
        hooks:                 body.hooks ?? Prisma.DbNull,
        calendar_id:             body.calendar_id ?? null,
        context_visibility:      body.context_visibility ?? Prisma.DbNull,
        created_by:              createdBy,
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

    // Attach the live deploy slot (PoolSkillSlot.current) so consumers like the
    // bootstrap can provision instances from the deploy — skill_id + concurrent
    // sessions — instead of from legacy agent_types. Fase 3b.
    const currentSlots = await (prisma as any).poolSkillSlot.findMany({
      where: { tenant_id: tenantId, slot: "current" },
    }) as Array<{ pool_id: string; skill_id: string | null; config_json: unknown }>
    const slotByPool = new Map(
      currentSlots
        .filter((s) => !!s.skill_id)
        .map((s) => [s.pool_id, s] as const),
    )

    const formatted = pools.map((p) => {
      const base = _formatPool(p as unknown as Record<string, unknown>)
      const slot = slotByPool.get((p as { pool_id: string }).pool_id)
      if (slot && slot.skill_id) {
        const cfg = (slot.config_json ?? {}) as Record<string, unknown>
        const mcs = cfg["max_concurrent_sessions"]
        base["deployed_skill_id"] = slot.skill_id
        base["deployed_max_concurrent_sessions"] =
          typeof mcs === "number" && mcs >= 1 ? Math.floor(mcs) : 1
      }
      return base
    })

    return res.json({ pools: formatted, total: formatted.length })
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
        ...(body.sla_target_ms           !== undefined && { sla_target_ms:           body.sla_target_ms }),
        ...(body.webhook_skill_id        !== undefined && { webhook_skill_id:        body.webhook_skill_id }),
        ...(body.max_concurrent_sessions !== undefined && { max_concurrent_sessions: body.max_concurrent_sessions }),
        ...(body.max_reply_time_ms       !== undefined && { max_reply_time_ms:       body.max_reply_time_ms }),
        ...(body.routing_expression      !== undefined && { routing_expression:      body.routing_expression }),
        ...(body.evaluation_template_id !== undefined && { evaluation_template_id: body.evaluation_template_id }),
        ...(body.supervisor_config     !== undefined && { supervisor_config:     body.supervisor_config }),
        ...(body.queue_config          !== undefined && { queue_config:          body.queue_config }),
        ...(body.mentionable_pools     !== undefined && { mentionable_pools:     body.mentionable_pools }),
        ...(body.agent_groups          !== undefined && { agent_groups:          body.agent_groups }),
        ...(body.hooks                 !== undefined && { hooks:                 body.hooks }),
        ...(body.calendar_id              !== undefined && { calendar_id:              body.calendar_id }),
        ...(body.context_visibility       !== undefined && { context_visibility:       body.context_visibility }),
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
// GET /v1/pools/:pool_id/mentionable-agents
// Returns AI agent types available for @mention / Delegar in this pool,
// derived from pool.mentionable_pools: Record<alias, pool_id>.
// Response: { agents: { agent_type_id, pool_id, description? }[] }
// ─────────────────────────────────────────────
poolsRouter.get("/:pool_id/mentionable-agents", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const poolId   = req.params["pool_id"]!

    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    // mentionable_pools: Record<alias, pool_id>  e.g. { copilot: "copilot_sac", auth: "auth_ia" }
    const mentionablePools = pool.mentionable_pools as Record<string, string> | null
    if (!mentionablePools || Object.keys(mentionablePools).length === 0) {
      return res.json({ agents: [] })
    }

    const targetPoolIds = Object.values(mentionablePools)

    // AgentType retired: source each specialist from its target pool's current
    // deploy slot (PoolSkillSlot.current → skill_id). The synthesized agent_type_id
    // equals the skill_id (deploy-driven), so it still matches the live participant
    // and formats to a readable name in the UI.
    const currentSlots = await (prisma as any).poolSkillSlot.findMany({
      where: { tenant_id: tenantId, slot: "current", pool_id: { in: targetPoolIds } },
    }) as Array<{ pool_id: string; skill_id: string | null }>
    const skillByPool = new Map<string, string>(
      currentSlots.filter(s => !!s.skill_id).map(s => [s.pool_id, s.skill_id as string]),
    )

    // Build alias-indexed response: one entry per alias (key in mentionable_pools)
    // so the UI can construct @alias mentions (not @agent_type_id which is not a valid alias)
    const agents: Array<{
      alias:         string;
      agent_type_id: string;
      pool_id:       string;
    }> = []

    for (const [alias, targetPoolId] of Object.entries(mentionablePools)) {
      const skillId = skillByPool.get(targetPoolId)
      if (!skillId) continue
      agents.push({
        alias,
        agent_type_id: skillId,   // deploy-driven: agent_type_id == skill_id
        pool_id:       targetPoolId,
      })
    }

    return res.json({ agents })
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
