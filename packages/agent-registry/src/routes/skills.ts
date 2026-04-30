/**
 * routes/skills.ts
 * CRUD de skills — spec 4.7
 *
 * Validações cruzadas:
 * - classification.type === "orchestrator" requer campo flow
 * - mcp_server em tools deve estar registrado no tenant
 */

import { Router, Request, Response, NextFunction } from "express"
import { prisma, Prisma }      from "../db"
import { CreateSkillSchema, UpdateSkillSchema, validateMaskedBlock } from "../validators/skill"
import { publishRegistryChanged } from "../infra/kafka"

export const skillsRouter = Router()

// ─────────────────────────────────────────────
// POST /v1/skills
// ─────────────────────────────────────────────
skillsRouter.post("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId  = _getTenantId(req)
    const createdBy = _getUserId(req)
    const body      = CreateSkillSchema.parse(req.body)

    // ── Validação cruzada: mcp_servers das tools estão registrados ──
    if (body.tools && body.tools.length > 0) {
      const mcpServers = [...new Set(body.tools.map(t => t.mcp_server))]
      // TODO: consultar tabela mcp_servers do tenant
      // Por ora, aceita qualquer mcp_server — implementar quando mcp_servers table existir
      void mcpServers
    }

    // ── Validação de bloco masked: reason step proibido dentro de begin/end_transaction ──
    if (body.flow) {
      const maskedErrors = validateMaskedBlock(body.flow)
      if (maskedErrors.length > 0) {
        return res.status(422).json({
          error:   "invalid_masked_block",
          details: maskedErrors,
        })
      }
    }

    // ── Verificar duplicata ──
    const existing = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: body.skill_id, tenant_id: tenantId } },
    })
    if (existing) {
      return res.status(409).json({
        error: "skill_id já registrado — crie uma nova versão (ex: _v2)",
      })
    }

    const skill = await prisma.skill.create({
      data: {
        skill_id:         body.skill_id,
        tenant_id:        tenantId,
        name:             body.name,
        version:          body.version,
        description:      body.description,
        classification:   body.classification,
        instruction:      (body.instruction ?? null) as unknown as Prisma.InputJsonValue,
        tools:            body.tools ?? [],
        interface_schema: body.interface    ?? Prisma.DbNull,
        evaluation:       body.evaluation   ?? Prisma.DbNull,
        knowledge_domains: body.knowledge_domains ?? [],
        compatibility:    body.compatibility ?? Prisma.DbNull,
        flow:             body.flow != null ? (body.flow as unknown as Prisma.InputJsonValue) : Prisma.DbNull,
        created_by:       createdBy,
      },
    })

    return res.status(201).json(_formatSkill(skill))
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/skills
// ─────────────────────────────────────────────
skillsRouter.get("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const type     = req.query["type"]     as string | undefined
    const vertical = req.query["vertical"] as string | undefined
    const domain   = req.query["domain"]   as string | undefined

    const skills = await prisma.skill.findMany({
      where: {
        tenant_id: tenantId,
        status:    "active",
        ...(type     && { classification: { path: ["type"],     equals: type } }),
        ...(vertical && { classification: { path: ["vertical"], equals: vertical } }),
        ...(domain   && { classification: { path: ["domain"],   equals: domain } }),
      },
      orderBy: { created_at: "asc" },
    })

    return res.json({ skills: skills.map(_formatSkill), total: skills.length })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/skills/:skill_id
// ─────────────────────────────────────────────
skillsRouter.get("/:skill_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const skill    = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: req.params["skill_id"]!, tenant_id: tenantId } },
    })

    if (!skill) return res.status(404).json({ error: "Skill não encontrada" })
    return res.json(_formatSkill(skill))
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// PUT /v1/skills/:skill_id  — replace flow (upsert-style)
// ─────────────────────────────────────────────
skillsRouter.put("/:skill_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId  = _getTenantId(req)
    const skillId   = req.params["skill_id"]!
    const body      = CreateSkillSchema.parse({ ...req.body, skill_id: skillId })

    // ── Validação de bloco masked ──
    if (body.flow) {
      const maskedErrors = validateMaskedBlock(body.flow)
      if (maskedErrors.length > 0) {
        return res.status(422).json({
          error:   "invalid_masked_block",
          details: maskedErrors,
        })
      }
    }

    const _upsertUpdate = {
      name:             body.name,
      version:          body.version,
      description:      body.description,
      classification:   body.classification,
      instruction:      (body.instruction ?? null) as unknown as Prisma.InputJsonValue,
      tools:            body.tools ?? [],
      interface_schema: body.interface    ?? Prisma.DbNull,
      evaluation:       body.evaluation   ?? Prisma.DbNull,
      knowledge_domains: body.knowledge_domains ?? [],
      compatibility:    body.compatibility ?? Prisma.DbNull,
      flow:             body.flow != null ? (body.flow as unknown as Prisma.InputJsonValue) : Prisma.DbNull,
      status:           "active",
      // deploy_status intentionally NOT updated on save — only the deploy action changes it
    }
    const _upsertCreate = {
      ..._upsertUpdate,
      skill_id:      skillId,
      tenant_id:     tenantId,
      deploy_status: "draft",   // new skills always start as drafts — field added in migration 20260430000000
      created_by:    _getUserId(req),
    }
    const skill = await prisma.skill.upsert({
      where:  { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
      update: _upsertUpdate as any,
      create: _upsertCreate as any,
    })

    // Notify orchestrator-bridge to invalidate its skill cache for this skill_id
    await publishRegistryChanged(tenantId, "skill", skillId, "updated")

    return res.json(_formatSkill(skill))
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// DELETE /v1/skills/:skill_id
// ─────────────────────────────────────────────
skillsRouter.delete("/:skill_id", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const skillId  = req.params["skill_id"]!

    const existing = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    if (!existing) return res.status(404).json({ error: "Skill não encontrada" })

    await prisma.skill.delete({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })

    // Notify orchestrator-bridge to invalidate its skill cache for this skill_id
    await publishRegistryChanged(tenantId, "skill", skillId, "deleted")

    return res.status(204).send()
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
function _formatSkill(skill: Record<string, unknown>): Record<string, unknown> {
  const { id: _id, interface_schema, ...rest } = skill
  return { ...rest, interface: interface_schema }
}

// ─────────────────────────────────────────────
// POST /v1/skills/:skill_id/deploy
// Deploys (publishes) a skill to specified pools.
// Sets deploy_status → "published", records a SkillDeployment entry,
// and triggers hot-reload cache invalidation.
// ─────────────────────────────────────────────
skillsRouter.post("/:skill_id/deploy", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId  = _getTenantId(req)
    const userId    = _getUserId(req)
    const skillId   = req.params["skill_id"]!
    const { pool_ids, notes } = req.body as { pool_ids?: string[]; notes?: string }

    if (!Array.isArray(pool_ids) || pool_ids.length === 0) {
      return res.status(400).json({ error: "pool_ids deve ser um array não-vazio de pool_id" })
    }

    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    if (!skill) return res.status(404).json({ error: "Skill não encontrada" })

    const now = new Date()

    // Mark skill as published and record deployment in a transaction
    const [updatedSkill, deployment] = await prisma.$transaction([
      prisma.skill.update({
        where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
        data: {
          deploy_status: "published",
          published_at:  now,
        } as any,  // deploy_status/published_at are new fields — Prisma client not yet regenerated
      }),
      (prisma as any).skillDeployment.create({
        data: {
          skill_id:      skillId,
          tenant_id:     tenantId,
          version:       (skill as unknown as Record<string, unknown>)["version"] as string,
          pool_ids,
          yaml_snapshot: ((skill as unknown as Record<string, unknown>)["flow"] ?? null) as unknown as Prisma.InputJsonValue,
          deployed_by:   userId,
          deployed_at:   now,
          notes:         notes ?? null,
        },
      }),
    ])

    // Trigger orchestrator-bridge hot-reload
    await publishRegistryChanged(tenantId, "skill", skillId, "updated")

    return res.status(200).json({
      skill:      _formatSkill(updatedSkill as unknown as Record<string, unknown>),
      deployment: _formatDeployment(deployment as unknown as Record<string, unknown>),
    })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/skills/:skill_id/deployments
// Lists deployment history for a skill, newest first.
// ─────────────────────────────────────────────
skillsRouter.get("/:skill_id/deployments", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const skillId  = req.params["skill_id"]!
    const limit    = Math.min(parseInt((req.query["limit"] as string) ?? "50", 10), 200)

    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    if (!skill) return res.status(404).json({ error: "Skill não encontrada" })

    const deployments = await (prisma as any).skillDeployment.findMany({
      where:   { skill_id: skillId, tenant_id: tenantId },
      orderBy: { deployed_at: "desc" },
      take:    limit,
    })

    return res.json({
      deployments: (deployments as any[]).map((d: any) => _formatDeployment(d as Record<string, unknown>)),
      total: deployments.length,
    })
  } catch (err) {
    return next(err)
  }
})

function _formatDeployment(d: Record<string, unknown>): Record<string, unknown> {
  const { id: _id, ...rest } = d
  return { id: _id, ...rest }
}
