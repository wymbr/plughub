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
        max_reply_time_ms:     body.max_reply_time_ms ?? null,
        routing_expression:    body.routing_expression ?? Prisma.DbNull,
        evaluation_template_id: body.evaluation_template_id ?? null,
        supervisor_config:     body.supervisor_config ?? Prisma.DbNull,
        queue_config:          body.queue_config ?? Prisma.DbNull,
        mentionable_pools:     body.mentionable_pools ?? Prisma.DbNull,
        mentionable_journeys:  body.mentionable_journeys ?? Prisma.DbNull,
        agent_groups:          body.agent_groups ?? [],
        hooks:                 body.hooks ?? Prisma.DbNull,
        calendar_id:             body.calendar_id ?? null,
        context_visibility:      body.context_visibility ?? Prisma.DbNull,
        inbound_journey_resume:  body.inbound_journey_resume ?? false,
        authorized_journey_types: body.authorized_journey_types ?? [],
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
        ...(body.max_reply_time_ms     !== undefined && { max_reply_time_ms:     body.max_reply_time_ms }),
        ...(body.routing_expression    !== undefined && { routing_expression:    body.routing_expression }),
        ...(body.evaluation_template_id !== undefined && { evaluation_template_id: body.evaluation_template_id }),
        ...(body.supervisor_config     !== undefined && { supervisor_config:     body.supervisor_config }),
        ...(body.queue_config          !== undefined && { queue_config:          body.queue_config }),
        ...(body.mentionable_pools     !== undefined && { mentionable_pools:     body.mentionable_pools }),
        ...(body.mentionable_journeys  !== undefined && { mentionable_journeys:  body.mentionable_journeys }),
        ...(body.agent_groups          !== undefined && { agent_groups:          body.agent_groups }),
        ...(body.hooks                 !== undefined && { hooks:                 body.hooks }),
        ...(body.calendar_id              !== undefined && { calendar_id:              body.calendar_id }),
        ...(body.context_visibility       !== undefined && { context_visibility:       body.context_visibility }),
        ...(body.inbound_journey_resume      !== undefined && { inbound_journey_resume:      body.inbound_journey_resume }),
        ...(body.authorized_journey_types    !== undefined && { authorized_journey_types:    body.authorized_journey_types }),
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

    // Find all active non-human agent types belonging to the mentionable pools
    const agentTypes = await prisma.agentType.findMany({
      where: {
        tenant_id: tenantId,
        status:    "active",
        framework: { not: "human" },
        pools: {
          some: {
            pool: { pool_id: { in: targetPoolIds }, tenant_id: tenantId },
          },
        },
      },
      include: { pools: { include: { pool: true } } },
    })

    // Build alias-indexed response: one entry per alias (key in mentionable_pools)
    // so the UI can construct @alias mentions (not @agent_type_id which is not a valid alias)
    const agents: Array<{
      alias:         string;
      agent_type_id: string;
      pool_id:       string;
      description?:  string;
    }> = []

    for (const [alias, targetPoolId] of Object.entries(mentionablePools)) {
      const matchingAt = agentTypes.find(at =>
        (at.pools as any[]).some((atp: any) => atp.pool?.pool_id === targetPoolId)
      )
      if (!matchingAt) continue
      agents.push({
        alias,
        agent_type_id: matchingAt.agent_type_id,
        pool_id:       targetPoolId,
        description:   (matchingAt.capabilities as Record<string, unknown>)?.["description"] as string ?? undefined,
      })
    }

    return res.json({ agents })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/pools/:pool_id/mentionable-processes
// Returns Journey-starting skills available for manual invocation in this pool,
// derived from pool.mentionable_journeys: Record<alias, skill_id>.
//
// Response:
//   { processes: MentionableProcess[] }
//
// MentionableProcess:
//   alias               — key in mentionable_journeys (e.g. "portabilidade")
//   skill_id            — target skill (e.g. "skill_portabilidade_v1")
//   label               — skill.name
//   description         — skill.description
//   delegation_params   — DelegationSchema | null (from skill.delegation_input)
//   delegation_visibility — "all" | "agents_only" | null
//                          null  → show visibility radio, default agents_only
//                          value → locked; radio hidden in UI
//                          Read from skill.flow.delegation_visibility (top-level YAML field)
// ─────────────────────────────────────────────
poolsRouter.get("/:pool_id/mentionable-processes", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const poolId   = req.params["pool_id"]!

    const pool = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: poolId, tenant_id: tenantId } },
    })
    if (!pool) return res.status(404).json({ error: "Pool não encontrado" })

    // mentionable_journeys: Record<alias, skill_id>  e.g. { portabilidade: "skill_portabilidade_v1" }
    const mentionableJourneys = pool.mentionable_journeys as Record<string, string> | null
    if (!mentionableJourneys || Object.keys(mentionableJourneys).length === 0) {
      return res.json({ processes: [] })
    }

    const targetSkillIds = Object.values(mentionableJourneys)

    // Fetch active skills matching the declared skill_ids
    const skills = await prisma.skill.findMany({
      where: {
        tenant_id: tenantId,
        skill_id:  { in: targetSkillIds },
        status:    "active",
      },
      select: {
        skill_id:        true,
        name:            true,
        description:     true,
        delegation_input: true,
        flow:            true,
      },
    })

    // Index skills by skill_id for O(1) lookup
    const skillMap = new Map(skills.map(s => [s.skill_id, s]))

    const processes: Array<{
      alias:                string;
      skill_id:             string;
      label:                string;
      description:          string;
      delegation_params:    unknown;
      delegation_visibility: string | null;
    }> = []

    for (const [alias, targetSkillId] of Object.entries(mentionableJourneys)) {
      const skill = skillMap.get(targetSkillId)
      if (!skill) continue

      // delegation_visibility declared as top-level field in skill YAML → stored in flow JSON
      const flow = skill.flow as Record<string, unknown> | null
      const delegationVisibility =
        (flow?.["delegation_visibility"] as "all" | "agents_only" | undefined) ?? null

      processes.push({
        alias,
        skill_id:             skill.skill_id,
        label:                skill.name,
        description:          skill.description,
        delegation_params:    skill.delegation_input ?? null,   // DelegationSchema | null
        delegation_visibility: delegationVisibility,
      })
    }

    return res.json({ processes })
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
