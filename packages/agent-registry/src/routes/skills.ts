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
import { CreateSkillSchema, UpdateSkillSchema, validateMaskedBlock, validateMaskedTypeRefs } from "../validators/skill"
import { publishRegistryChanged } from "../infra/kafka"
import { config } from "../config"

// ── Config-param `source` lint (advisory, non-blocking) ──────────────────────
// A config_param's `source` is an OPEN string interpreted only by the deploy UI
// (known values → combo; unknown → text input). A typo therefore degrades
// silently at deploy. We catch it at PUBLISH (the authoring moment): unknown
// sources emit a non-blocking warning (logged + returned as `config_param_warnings`)
// so the author sees it without the schema having to reject an open field — a
// stale UI could legitimately not know a newer source, so we never hard-fail.
// Keep in sync with platform-ui AgentFlowDeployPage CONFIG_PARAM_SOURCES.
const KNOWN_CONFIG_PARAM_SOURCES = new Set(["dialogforms", "pools", "skills"])

function configParamSourceWarnings(
  params: ReadonlyArray<{ key?: string | undefined; source?: string | undefined }> | undefined,
): string[] {
  const warnings: string[] = []
  for (const p of params ?? []) {
    if (p.source && !KNOWN_CONFIG_PARAM_SOURCES.has(p.source)) {
      warnings.push(
        `config_param "${p.key ?? "?"}" declares unknown source "${p.source}" — ` +
        `the deploy UI will fall back to a text input (possible typo; known sources: ` +
        `${[...KNOWN_CONFIG_PARAM_SOURCES].join(", ")})`,
      )
    }
  }
  return warnings
}

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
        error: "skill_id já existe — edite/atualize o skill (PUT) ou escolha outro nome. " +
               "Versões nascem no deploy, não renomeando o skill.",
      })
    }

    const skill = await prisma.skill.create({
      data: {
        skill_id:         body.skill_id,
        tenant_id:        tenantId,
        name:             body.name,
        version:          body.version ?? "",   // rótulo livre opcional; "" quando ausente (coluna NOT NULL)
        description:      body.description,
        classification:   body.classification,
        instruction:      (body.instruction ?? null) as unknown as Prisma.InputJsonValue,
        tools:            body.tools ?? [],
        interface_schema: body.interface    ?? Prisma.DbNull,
        config_params:    body.config_params ?? Prisma.DbNull,
        evaluation:       body.evaluation   ?? Prisma.DbNull,
        knowledge_domains: body.knowledge_domains ?? [],
        compatibility:    body.compatibility ?? Prisma.DbNull,
        // UMA definição, sem rascunho (2026-07-13) — igual ao PUT. O create GRAVA a
        // definição em `flow`; não existe mais o par flow/flow_draft.
        //
        // Antes (Fase B) isto gravava `flow: null` + `flow_draft: <def>`, apostando que
        // o deploy promoveria draft→flow. Com produção rodando o SNAPSHOT DO SLOT, o
        // `flow` é a definição-fonte que o set-next congela — deixá-lo nulo criava um
        // skill **sem definição publicável**: o slot congelava `yaml_snapshot: null`,
        // e o bridge reportava "pool sem slot" para um pool que tinha slot.
        flow:             body.flow != null ? (body.flow as unknown as Prisma.InputJsonValue) : Prisma.DbNull,
        flow_draft:       Prisma.DbNull,                    // conceito morto — sempre limpo
        flow_model:       _computeFlowModel(body.flow),
        delegation_input: (body as any).delegation_input != null
          ? ((body as any).delegation_input as unknown as Prisma.InputJsonValue)
          : Prisma.DbNull,
        // Vestigial (igual ao PUT): não há mais draft/published — a definição é a
        // definição, e o que roda é o snapshot do slot. Fica "published" para não
        // deixar a coluna divergir entre os dois caminhos de escrita.
        deploy_status:    "published",
        created_by:       createdBy,
      } as any,
    })

    const warnings = configParamSourceWarnings(body.config_params)
    for (const w of warnings) console.warn(`[skills] ${body.skill_id}: ${w}`)
    return res.status(201).json({
      ..._formatSkill(skill),
      ...(warnings.length ? { config_param_warnings: warnings } : {}),
    })
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
// GET /v1/skills/:skill_id/delegation-schema
// Deploy-driven replacement for the retired agent-type delegation-schema:
// delegation_input lives on the skill; visibility defaults to null (the UI then
// shows the visibility radio, default agents_only).
// ─────────────────────────────────────────────
skillsRouter.get("/:skill_id/delegation-schema", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const skill    = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: req.params["skill_id"]!, tenant_id: tenantId } },
      select: { skill_id: true, delegation_input: true } as any,
    }) as { skill_id: string; delegation_input: unknown } | null
    if (!skill) return res.status(404).json({ error: "Skill não encontrada" })
    return res.json({
      skill_id:              skill.skill_id,
      delegation_input:      skill.delegation_input ?? null,
      delegation_visibility: null,
    })
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

    // ── LÁPIDE — `agent_role` recusado NOMEANDO (CAP-03, 2026-09-01) ─────────
    //
    // O campo foi removido do modelo. Zod ignora chave desconhecida em silêncio,
    // então quem continuasse mandando `{"agent_role": "evaluator"}` receberia 200
    // sobre um no-op e acharia que declarou algo. Foi o custo MEDIDO da remoção do
    // `unrestricted` (2026-08-31): `PATCH {"unrestricted":true}` → 200.
    //
    // A pergunta é feita ao corpo CRU pela mesma razão de antes: depois do parse a
    // chave desconhecida já sumiu, e não haveria o que recusar.
    if (
      req.body != null &&
      typeof req.body === "object" &&
      "agent_role" in (req.body as Record<string, unknown>)
    ) {
      return res.status(422).json({
        error:  "agent_role_removed",
        detail:
          "O campo `agent_role` foi REMOVIDO em 2026-09-01. Ele tinha um consumidor " +
          "só — o gate de evaluation_context_get/evaluation_submit — e esse gate saiu " +
          "por não impedir cenário nenhum (lia o papel do participant_id do INPUT). " +
          "Remova o campo do payload: mandá-lo não declara mais nada. A verificação de " +
          "que um pool avaliador roda um flow de avaliação vive agora no create/update " +
          "de campanha (evaluation-api). Ver docs/adr/adr-remove-agent-role-axis.md.",
      })
    }

    // ── Validação de bloco masked ──
    if (body.flow) {
      const maskedErrors = validateMaskedBlock(body.flow)
      if (maskedErrors.length > 0) {
        return res.status(422).json({
          error:   "invalid_masked_block",
          details: maskedErrors,
        })
      }

      // ── T5 — portão de DEPLOY do `masked` tipado (ADR D3) ──────────────────
      // Só busca o catálogo quando o flow declara ALGUM tipo (string). Flow sem
      // declaração tipada não paga a dependência do config-api para ser salvo —
      // é o que impede este portão de virar acoplamento geral.
      const typeErrors = await validateMaskedTypeRefs(body.flow, {
        tenantId:     tenantId,
        configApiUrl: config.config_api_url,
      })
      if (typeErrors.length > 0) {
        return res.status(422).json({
          error:   "invalid_masked_type",
          details: typeErrors,
        })
      }
    }

    // ── UMA definição, sem rascunho (2026-07-13) ─────────────────────────────
    // Antes havia dois canais: o editor gravava `flow_draft` e o sync/deploy gravava
    // `flow` (produção). Esse desenho tinha DOIS defeitos:
    //   (a) o RegistrySyncer publicava com `x-skill-publish`, o que gravava
    //       `{ flow, flow_draft: null }` → a cada boot do bridge o rascunho do editor
    //       era APAGADO. O editor perdia trabalho silenciosamente.
    //   (b) o draft só existia para impedir que edições vazassem para produção —
    //       mas produção agora roda EXCLUSIVAMENTE o snapshot do slot do pool
    //       (fallback legado removido no bridge), então não há o que vazar.
    //
    // Modelo atual: **uma definição editável** (`flow`, com `updated_at`) + **cópia
    // imutável no deploy** (snapshot do slot). Salvar NÃO afeta o que roda; só o
    // deploy (set-next → promote) muda produção. É o modelo arquivo-fonte × artefato.
    //
    // `x-skill-publish` vira no-op (mantido só para não quebrar chamadores antigos).
    const flowJson = body.flow != null ? (body.flow as unknown as Prisma.InputJsonValue) : Prisma.DbNull

    const _flowFields = {
      flow:       flowJson,
      flow_draft: Prisma.DbNull,                    // conceito morto — sempre limpo
      flow_model: _computeFlowModel(body.flow),
    }

    const _upsertUpdate = {
      name:             body.name,
      version:          body.version ?? "",   // rótulo livre opcional; "" quando ausente (coluna NOT NULL)
      description:      body.description,
      classification:   body.classification,
      instruction:      (body.instruction ?? null) as unknown as Prisma.InputJsonValue,
      tools:            body.tools ?? [],
      interface_schema: body.interface    ?? Prisma.DbNull,
      config_params:    body.config_params ?? Prisma.DbNull,
      evaluation:       body.evaluation   ?? Prisma.DbNull,
      knowledge_domains: body.knowledge_domains ?? [],
      compatibility:    body.compatibility ?? Prisma.DbNull,
      ..._flowFields,
      delegation_input: (body as any).delegation_input != null
        ? ((body as any).delegation_input as unknown as Prisma.InputJsonValue)
        : Prisma.DbNull,
      status:           "active",
      // Vestigial: não há mais draft/published — a definição é a definição.
      deploy_status:    "published",
    }
    const _upsertCreate = {
      ..._upsertUpdate,
      skill_id:      skillId,
      tenant_id:     tenantId,
      deploy_status: "published",
      created_by:    _getUserId(req),
    }
    const skill = await prisma.skill.upsert({
      where:  { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
      update: _upsertUpdate as any,
      create: _upsertCreate as any,
    })

    // Notify orchestrator-bridge to invalidate its skill cache for this skill_id
    await publishRegistryChanged(tenantId, "skill", skillId, "updated")

    const warnings = configParamSourceWarnings(body.config_params)
    for (const w of warnings) console.warn(`[skills] ${skillId}: ${w}`)
    return res.json({
      ..._formatSkill(skill),
      ...(warnings.length ? { config_param_warnings: warnings } : {}),
    })
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
  // UMA definição, sem rascunho (2026-07-13). `flow` É a definição; salvar não muda o
  // que roda (produção = snapshot do slot do pool). `updated_at` é o timestamp que a
  // tela de Deploy compara com o `set_at` do slot para dizer "salvo mas não implantado"
  // — o que substitui o antigo flag `unpublished_draft`.
  //
  // `unpublished_draft` fica exposto como sempre-false por retrocompat com a UI antiga,
  // até o cleanup do schema (drop de flow_draft/deploy_status).
  return { ...rest, interface: interface_schema, unpublished_draft: false }
  // delegation_input is forwarded as-is via ...rest
}

/**
 * Derives the execution model from the skill's flow definition.
 *
 * Returns "workflow" when the flow contains at least one step of type
 * "suspend" or "collect" — these steps require the workflow-api persistence
 * and resume machinery (multi-session, async).
 *
 * Returns "agent" for all other flows, which execute synchronously within
 * a single agent session turn.
 */
function _computeFlowModel(flow: unknown): "agent" | "workflow" {
  if (!flow || typeof flow !== "object" || Array.isArray(flow)) return "agent"
  const steps = (flow as Record<string, unknown>).steps
  if (!Array.isArray(steps)) return "agent"
  const hasWorkflowStep = steps.some((s: unknown) => {
    if (!s || typeof s !== "object" || Array.isArray(s)) return false
    const t = (s as Record<string, unknown>).type
    return t === "suspend" || t === "collect"
  })
  return hasWorkflowStep ? "workflow" : "agent"
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

    // Skill Versioning Fase B: o deploy é o ÚNICO a escrever produção. Promove
    // RASCUNHO → PRODUÇÃO (flow_draft → flow), limpa o draft (sem pendência) e
    // recomputa flow_model do que foi efetivamente deployado. Snapshot = o flow
    // deployado. O bridge lê `flow` (produção) → hot-reload pega o conteúdo novo.
    const skillRec      = skill as unknown as Record<string, unknown>
    const deployedFlow  = (skillRec["flow_draft"] ?? skillRec["flow"]) ?? null

    const [updatedSkill, deployment] = await prisma.$transaction([
      prisma.skill.update({
        where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
        data: {
          flow:          (deployedFlow ?? Prisma.DbNull) as unknown as Prisma.InputJsonValue,
          flow_draft:    Prisma.DbNull,            // draft promovido → sem pendência
          flow_model:    _computeFlowModel(deployedFlow),
          deploy_status: "published",
          published_at:  now,
        } as any,  // deploy_status/published_at/flow_draft — Prisma client regenerated on build
      }),
      (prisma as any).skillDeployment.create({
        data: {
          skill_id:      skillId,
          tenant_id:     tenantId,
          version:       (skillRec["version"] as string) ?? "",
          pool_ids,
          yaml_snapshot: (deployedFlow ?? null) as unknown as Prisma.InputJsonValue,
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

// ─────────────────────────────────────────────
// GET /v1/skills/:skill_id/deployments/scheduled
// Returns pending scheduled workflow deploy instances for a skill.
// Proxies to workflow-api GET /v1/workflow/instances?flow_id=skill_scheduled_deploy_v1&status=suspended
// filtered to instances whose context.skill_id matches.
// ─────────────────────────────────────────────
skillsRouter.get("/:skill_id/deployments/scheduled", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const skillId  = req.params["skill_id"]!

    // Verify skill exists
    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    if (!skill) return res.status(404).json({ error: "Skill não encontrada" })

    // Proxy to workflow-api
    const workflowUrl = `${config.workflow_api_url}/v1/workflow/instances?flow_id=skill_scheduled_deploy_v1&status=suspended&tenant_id=${encodeURIComponent(tenantId)}&limit=50`
    let workflowInstances: Record<string, unknown>[] = []
    try {
      const wfRes = await fetch(workflowUrl, {
        headers: { "x-tenant-id": tenantId },
      })
      if (wfRes.ok) {
        const body = await wfRes.json() as { instances?: Record<string, unknown>[] }
        workflowInstances = body.instances ?? []
      }
    } catch {
      // Workflow-api unavailable — return empty list gracefully
    }

    // Filter to instances whose pipeline_state.contact_context.skill_id matches
    const relevant = workflowInstances.filter((inst) => {
      try {
        const ctx = (inst["pipeline_state"] as Record<string, unknown>)?.["contact_context"] as Record<string, unknown> | undefined
        return ctx?.["skill_id"] === skillId
      } catch {
        return false
      }
    })

    return res.json({
      skill_id: skillId,
      scheduled_deploys: relevant.map((inst) => {
        const ctx = ((inst["pipeline_state"] as Record<string, unknown>)?.["contact_context"] ?? {}) as Record<string, unknown>
        return {
          workflow_instance_id: inst["id"],
          skill_id:    ctx["skill_id"],
          pool_ids:    ctx["pool_ids"],
          scheduled_at: inst["resume_expires_at"],
          deployed_by:  ctx["deployed_by"],
          notes:        ctx["deploy_notes"],
          status:       inst["status"],
          created_at:   inst["created_at"],
        }
      }),
      total: relevant.length,
    })
  } catch (err) {
    return next(err)
  }
})

// ─────────────────────────────────────────────
// GET /v1/skills/:skill_id/handoff-status
// Returns the count of sessions still active on the previous skill version.
// Used by the Graceful Handoff Monitor UI to show deploy convergence progress.
// ─────────────────────────────────────────────
skillsRouter.get("/:skill_id/handoff-status", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId = _getTenantId(req)
    const skillId  = req.params["skill_id"]!

    // Verify skill exists and get deploy info
    const skill = await prisma.skill.findUnique({
      where: { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
    })
    if (!skill) return res.status(404).json({ error: "Skill não encontrada" })

    // Get the most recent published deployment
    const latestDeploy = await (prisma as any).skillDeployment.findFirst({
      where:   { skill_id: skillId, tenant_id: tenantId },
      orderBy: { deployed_at: "desc" },
    }) as Record<string, unknown> | null

    if (!latestDeploy) {
      return res.json({
        skill_id:       skillId,
        deployed:       false,
        active_sessions: 0,
        pool_ids:       [],
        deployed_at:    null,
      })
    }

    const deployedAt = latestDeploy["deployed_at"] as string
    const poolIds    = (latestDeploy["pool_ids"] as string[]) ?? []

    // Query analytics-api for sessions still active in affected pools started before deploy
    let activeSessionCount = 0
    if (poolIds.length > 0) {
      try {
        const params = new URLSearchParams({
          tenant_id: tenantId,
          to_dt:     deployedAt,          // sessions started before deploy
          page_size: "1",                 // we only need the total count
        })
        for (const pid of poolIds) params.append("pool_id", pid)

        const analyticsUrl = `${config.analytics_api_url}/reports/sessions?${params.toString()}`
        // Credencial de serviço (2026-08-30): esta chamada era ANÔNIMA e a
        // analytics-api passou a exigir credencial em 2026-08-29.
        const aRes = await fetch(analyticsUrl, {
          headers: config.analytics_service_token
            ? { "X-Service-Token": config.analytics_service_token,
                "X-Service-Name":  "agent-registry" }
            : {},
        })
        if (aRes.ok) {
          const aBody = await aRes.json() as { meta?: { total?: number }; total?: number }
          activeSessionCount = aBody.meta?.total ?? aBody.total ?? 0
        } else {
          // ⚠️ NÃO fica mudo. Este endpoint existe para DEPLOY SEGURO: devolver 0
          // sem dizer por quê faz uma promoção com sessões vivas parecer segura, e
          // foi exatamente assim que o 401 passou despercebido.
          console.warn(
            `handoff-status: analytics-api respondeu ${aRes.status} — ` +
            `active_sessions vai como 0 e NAO reflete a realidade (skill=${skillId})`,
          )
        }
      } catch (err) {
        // Idem: indisponibilidade tambem nao pode virar "0 sessoes" em silencio.
        console.warn(
          `handoff-status: analytics-api inalcancavel (${String(err)}) — ` +
          `active_sessions vai como 0 e NAO reflete a realidade (skill=${skillId})`,
        )
      }
    }

    return res.json({
      skill_id:         skillId,
      deployed:         true,
      active_sessions:  activeSessionCount,
      pool_ids:         poolIds,
      deployed_at:      deployedAt,
      deployment_id:    latestDeploy["id"],
      deployed_by:      latestDeploy["deployed_by"],
    })
  } catch (err) {
    return next(err)
  }
})
