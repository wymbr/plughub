/**
 * routes/import.ts
 * Endpoint de import de repositório GitAgent.
 * Spec: PlugHub v24.0 seção 4.9.6
 *
 * POST /v1/agent-types/import
 * Recebe URL ou path local, parseia via @plughub/gitagent,
 * registra AgentType + Skills via upsert.
 */

import { Router, Request, Response, NextFunction } from "express"
import { execSync }             from "child_process"
import { mkdtempSync, rmSync, existsSync } from "fs"
import { tmpdir }               from "os"
import { join }                 from "path"
import { z }                    from "zod"
import { prisma }               from "../db"

export const importRouter = Router()

const ImportRequestSchema = z.object({
  repository_url: z.string().url().optional(),
  local_path:     z.string().optional(),
  branch:         z.string().default("main"),
  auto_update:    z.boolean().default(false),
})

importRouter.post("/", async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tenantId  = (req.headers["x-tenant-id"] as string) ?? "tenant_default"
    const createdBy = (req.headers["x-user-id"]   as string) ?? "system"
    const body      = ImportRequestSchema.parse(req.body)

    if (!body.repository_url && !body.local_path) {
      return res.status(422).json({ error: "Forneça repository_url ou local_path" })
    }

    const isTemp  = !body.local_path
    const repoPath = body.local_path
      ?? _cloneRepo(body.repository_url!, body.branch)

    try {
      // Validar estrutura mínima
      const missing = ["agent.yaml", "instructions.md"]
        .filter(f => !existsSync(join(repoPath, f)))
      if (missing.length > 0) {
        return res.status(422).json({ error: "Estrutura GitAgent inválida", missing })
      }

      const result = await _importRepo(repoPath, tenantId, createdBy)
      return res.status(201).json(result)
    } finally {
      if (isTemp) rmSync(repoPath, { recursive: true, force: true })
    }
  } catch (err) {
    return next(err)
  }
})

function _cloneRepo(url: string, branch: string): string {
  const tmp = mkdtempSync(join(tmpdir(), "plughub-import-"))
  execSync(`git clone --depth 1 --branch ${branch} ${url} ${tmp}`, { stdio: "pipe" })
  return tmp
}

async function _importRepo(repoPath: string, tenantId: string, createdBy: string) {
  // eslint-disable-next-line @typescript-eslint/ban-ts-comment
  // @ts-ignore — @plughub/gitagent é gerado pelo plughub-sdk regenerate; ausente em dev
  const { GitAgentParser } = await import("@plughub/gitagent") as any
  const parser  = new GitAgentParser()
  const parsed  = parser.parse(repoPath)
  const at      = parser.toAgentTypeRegistration(parsed)
  const flows   = parser.getFlowsAsJson(parsed)
  const errors: string[] = []
  const skillsRegistered: string[] = []
  const now = new Date().toISOString()

  // Upsert AgentType
  try {
    await prisma.agentType.upsert({
      where:  { agent_type_id_tenant_id: { agent_type_id: at.agent_type_id, tenant_id: tenantId } },
      create: {
        agent_type_id:           at.agent_type_id, tenant_id: tenantId,
        framework:               at.framework, execution_model: at.execution_model,
        max_concurrent_sessions: at.max_concurrent_sessions ?? 1,
        skills: at.skills ?? [], permissions: at.permissions ?? [],
        capabilities: at.capabilities ?? {},
        agent_classification: at.agent_classification ?? null,
        created_by: createdBy,
      },
      update: {
        framework: at.framework, execution_model: at.execution_model,
        max_concurrent_sessions: at.max_concurrent_sessions ?? 1,
        skills: at.skills ?? [], permissions: at.permissions ?? [],
        capabilities: at.capabilities ?? {},
        agent_classification: at.agent_classification ?? null,
      },
    })
  } catch (e) {
    errors.push(`AgentType: ${e instanceof Error ? e.message : String(e)}`)
  }

  // Upsert Skills de orquestração — um por flow
  for (const [flowName, flowJson] of Object.entries(flows)) {
    const skillId = `skill_${at.agent_type_id}_${flowName}_v1`
    try {
      await prisma.skill.upsert({
        where:  { skill_id_tenant_id: { skill_id: skillId, tenant_id: tenantId } },
        create: {
          skill_id: skillId, tenant_id: tenantId,
          name:     `${at.agent_type_id} — ${flowName}`,
          version: "1.0.0",
          description: `Flow ${flowName} importado via GitAgent`,
          classification: { type: "orchestrator" },
          instruction:    { prompt_id: `prompt_${at.agent_type_id}_v1` },
          flow:           flowJson as object,
          created_by:     createdBy,
        },
        update: { flow: flowJson as object },
      })
      skillsRegistered.push(skillId)
    } catch (e) {
      errors.push(`Skill ${skillId}: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  return {
    agent_type_id:        at.agent_type_id,
    skills_registered:    skillsRegistered,
    flows_registered:     Object.keys(flows),
    certification_status: errors.length === 0 ? "passed" : "failed",
    imported_at:          now,
    ...(errors.length > 0 && { errors }),
  }
}
