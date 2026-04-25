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
import { CreateSkillSchema, UpdateSkillSchema } from "../validators/skill"

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
        instruction:      body.instruction,
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

    const skill = await prisma.skill.upsert({
      where:  { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
      update: {
        name:             body.name,
        version:          body.version,
        description:      body.description,
        classification:   body.classification,
        instruction:      body.instruction,
        tools:            body.tools ?? [],
        interface_schema: body.interface    ?? Prisma.DbNull,
        evaluation:       body.evaluation   ?? Prisma.DbNull,
        knowledge_domains: body.knowledge_domains ?? [],
        compatibility:    body.compatibility ?? Prisma.DbNull,
        flow:             body.flow != null ? (body.flow as unknown as Prisma.InputJsonValue) : Prisma.DbNull,
        status:           "active",
      },
      create: {
        skill_id:         skillId,
        tenant_id:        tenantId,
        name:             body.name,
        version:          body.version,
        description:      body.description,
        classification:   body.classification,
        instruction:      body.instruction,
        tools:            body.tools ?? [],
        interface_schema: body.interface    ?? Prisma.DbNull,
        evaluation:       body.evaluation   ?? Prisma.DbNull,
        knowledge_domains: body.knowledge_domains ?? [],
        compatibility:    body.compatibility ?? Prisma.DbNull,
        flow:             body.flow != null ? (body.flow as unknown as Prisma.InputJsonValue) : Prisma.DbNull,
        created_by:       _getUserId(req),
      },
    })

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
