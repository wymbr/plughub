/**
 * routes/agent-types.ts
 * CRUD de tipos de agente — spec 4.5
 *
 * Validações cruzadas implementadas aqui:
 * - pools declarados devem existir no Agent Registry
 * - skill_ids referenciados devem existir no Skill Registry
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma, Prisma }         from "../db"
import { CreateAgentTypeSchema }  from "../validators/agent-type"
import { CanaryPatchSchema }      from "../validators/canary"
import type { SkillRef }          from "@plughub/schemas"

export const agentTypesRouter = Router()

// ─────────────────────────────────────────────
// POST /v1/agent-types
// ─────────────────────────────────────────────
agentTypesRouter.post("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId  = _getTenantId(req)
    const createdBy = _getUserId(req)
    const body      = CreateAgentTypeSchema.parse(req.body)

    // ── Validação cruzada 1: pools existem ──
    const poolsFound = await prisma.pool.findMany({
      where: {
        tenant_id: tenantId,
        pool_id:   { in: body.pools },
        status:    "active",
      },
    })
    const foundPoolIds = poolsFound.map(p => p.pool_id)
    const missingPools = body.pools.filter(p => !foundPoolIds.includes(p))
    if (missingPools.length > 0) {
      return res.status(422).json({
        error:   "pools_not_found",
        detail:  `Pools não encontrados neste tenant: ${missingPools.join(", ")}`,
        missing: missingPools,
      })
    }

    // ── Validação cruzada 2: skills existem ──
    const skillRefs = body.skills as SkillRef[]
    if (skillRefs.length > 0) {
      const skillIds   = skillRefs.map(s => s.skill_id)
      const skillsFound = await prisma.skill.findMany({
        where: {
          tenant_id: tenantId,
          skill_id:  { in: skillIds },
          status:    "active",
        },
      })
      const foundSkillIds  = skillsFound.map(s => s.skill_id)
      const missingSkills  = skillIds.filter(id => !foundSkillIds.includes(id))
      if (missingSkills.length > 0) {
        return res.status(422).json({
          error:   "skills_not_found",
          detail:  `Skills não encontradas neste tenant: ${missingSkills.join(", ")}`,
          missing: missingSkills,
        })
      }
    }

    // ── Verificar duplicata ──
    const existing = await prisma.agentType.findUnique({
      where: { agent_type_id_tenant_id: { agent_type_id: body.agent_type_id, tenant_id: tenantId } },
    })
    if (existing) {
      return res.status(409).json({
        error: "agent_type_id já registrado — crie uma nova versão (ex: _v2)",
      })
    }

    // ── Criar tipo e vincular aos pools ──
    const agentType = await prisma.agentType.create({
      data: {
        agent_type_id:          body.agent_type_id,
        tenant_id:              tenantId,
        framework:              body.framework,
        execution_model:        body.execution_model,
        role:                   body.role ?? "executor",
        max_concurrent_sessions: body.max_concurrent_sessions ?? 1,
        skills:                 body.skills,
        permissions:            body.permissions ?? [],
        capabilities:           body.capabilities ?? {},
        prompt_id:              body.prompt_id ?? null,
        agent_classification:   body.agent_classification ?? Prisma.DbNull,
        created_by:             createdBy,
        pools: {
          create: poolsFound.map(p => ({ pool_id: p.id })),
        },
      },
      include: { pools: { include: { pool: true } } },
    })

    return res.status(201).json(_formatAgentType(agentType))
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/agent-types
// ─────────────────────────────────────────────
agentTypesRouter.get("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const poolId   = req.query["pool_id"] as string | undefined
    const role     = req.query["role"]    as string | undefined

    const agentTypes = await prisma.agentType.findMany({
      where: {
        tenant_id: tenantId,
        status:    "active",
        ...(role   && { role }),
        ...(poolId && {
          pools: { some: { pool: { pool_id: poolId, tenant_id: tenantId } } },
        }),
      },
      include: { pools: { include: { pool: true } } },
      orderBy: { created_at: "asc" },
    })

    return res.json({ agent_types: agentTypes.map(_formatAgentType), total: agentTypes.length })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/agent-types/:agent_type_id
// ─────────────────────────────────────────────
agentTypesRouter.get("/:agent_type_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId  = _getTenantId(req)
    const agentType = await prisma.agentType.findUnique({
      where: {
        agent_type_id_tenant_id: {
          agent_type_id: req.params["agent_type_id"]!,
          tenant_id:     tenantId,
        },
      },
      include: { pools: { include: { pool: true } } },
    })

    if (!agentType) return res.status(404).json({ error: "Agent type não encontrado" })
    return res.json(_formatAgentType(agentType))
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// PATCH /v1/agent-types/:agent_type_id/canary
// Ajusta traffic_weight para progressão de canário.
// Progressão esperada: 0.10 → 0.20 → 0.50 → 1.00
// ─────────────────────────────────────────────
agentTypesRouter.patch("/:agent_type_id/canary", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const body     = CanaryPatchSchema.parse(req.body)

    const agentType = await prisma.agentType.findUnique({
      where: {
        agent_type_id_tenant_id: {
          agent_type_id: req.params["agent_type_id"]!,
          tenant_id:     tenantId,
        },
      },
    })
    if (!agentType) {
      return res.status(404).json({ error: "Agent type não encontrado" })
    }
    if (agentType.status !== "active") {
      return res.status(409).json({
        error: "canary_invalid_state",
        detail: `Agent type está ${agentType.status} — canário só é gerenciável em tipos ativos`,
      })
    }

    const updated = await prisma.agentType.update({
      where: { id: agentType.id },
      data:  { traffic_weight: body.traffic_weight },
    })

    return res.json({
      ..._formatAgentType(updated as unknown as Record<string, unknown>),
      canary: {
        traffic_weight:     updated.traffic_weight,
        progression_target: _nextCanaryWeight(updated.traffic_weight),
      },
    })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// DELETE /v1/agent-types/:agent_type_id/canary
// Rollback imediato:
//   1. Restaura versão anterior (weight → 1.0)
//   2. Arquiva versão atual (status → deprecated)
// ─────────────────────────────────────────────
agentTypesRouter.delete("/:agent_type_id/canary", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId     = _getTenantId(req)
    const agentTypeId  = req.params["agent_type_id"]!

    const current = await prisma.agentType.findUnique({
      where: {
        agent_type_id_tenant_id: { agent_type_id: agentTypeId, tenant_id: tenantId },
      },
    })
    if (!current) {
      return res.status(404).json({ error: "Agent type não encontrado" })
    }

    // Identifica versão anterior pela convenção {base}_v{n}
    let previousId: string | null = null
    let previousRestored = false
    const versionMatch = agentTypeId.match(/^(.+)_v(\d+)$/)
    if (versionMatch) {
      const [, base, vStr] = versionMatch
      const prevVersion    = parseInt(vStr!, 10) - 1
      if (prevVersion >= 1) {
        previousId = `${base}_v${prevVersion}`
        const previous = await prisma.agentType.findUnique({
          where: {
            agent_type_id_tenant_id: { agent_type_id: previousId, tenant_id: tenantId },
          },
        })
        if (previous) {
          await prisma.agentType.update({
            where: { id: previous.id },
            data:  { traffic_weight: 1.0 },
          })
          previousRestored = true
        }
      }
    }

    // Arquiva a versão canária
    await prisma.agentType.update({
      where: { id: current.id },
      data:  { status: "deprecated", traffic_weight: 0.0 },
    })

    return res.json({
      rolled_back:        agentTypeId,
      restored_to:        previousRestored ? previousId : null,
      previous_weight:    previousRestored ? 1.0 : null,
      archived_at:        new Date().toISOString(),
    })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────
function _getTenantId(req: Request): string {
  return (req.headers["x-tenant-id"] as string) ?? "tenant_default"
}
function _getUserId(req: Request): string {
  return (req.headers["x-user-id"] as string) ?? "system"
}
function _formatAgentType(at: Record<string, unknown>): Record<string, unknown> {
  const { id: _id, ...rest } = at
  return rest
}

/** Próximo weight na progressão canária padrão: 0.10 → 0.20 → 0.50 → 1.00 */
function _nextCanaryWeight(current: number): number | null {
  if (current < 0.20) return 0.20
  if (current < 0.50) return 0.50
  if (current < 1.00) return 1.00
  return null
}
