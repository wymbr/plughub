/**
 * import.ts
 * Importa repositório GitAgent para o Agent Registry.
 * Spec: PlugHub v24.0 seção 4.9.6
 */

import { execSync }          from "child_process"
import { mkdtempSync, rmSync } from "fs"
import { tmpdir }            from "os"
import { join }              from "path"
import { GitAgentParser }    from "./yaml_parser"

export interface ImportOptions {
  repositoryUrl?: string
  localPath?:     string
  branch?:        string
  tenantId:       string
  registryUrl:    string
  apiKey:         string
  autoUpdate?:    boolean
}

export interface ImportResult {
  agent_type_id:        string
  skills_registered:    string[]
  flows_registered:     string[]
  certification_status: "passed" | "failed"
  imported_at:          string
  errors?:              string[]
}

export class GitAgentImporter {
  private readonly parser = new GitAgentParser()

  async import(options: ImportOptions): Promise<ImportResult> {
    const repoPath = options.localPath
      ?? await this._clone(options.repositoryUrl!, options.branch ?? "main")
    const isTemp = !options.localPath

    try {
      return await this._doImport(repoPath, options)
    } finally {
      if (isTemp) rmSync(repoPath, { recursive: true, force: true })
    }
  }

  private async _doImport(repoPath: string, opts: ImportOptions): Promise<ImportResult> {
    const now    = new Date().toISOString()
    const parsed = this.parser.parse(repoPath)
    const at     = this.parser.toAgentTypeRegistration(parsed)
    const flows  = this.parser.getFlowsAsJson(parsed)
    const errors: string[] = []

    // Registrar AgentType
    const atRes = await fetch(`${opts.registryUrl}/v1/agent-types`, {
      method:  "POST",
      headers: { "Content-Type": "application/json", "x-tenant-id": opts.tenantId, "x-api-key": opts.apiKey },
      body:    JSON.stringify(at),
    })
    if (!atRes.ok && atRes.status !== 409) {
      errors.push(`AgentType: ${atRes.status}`)
    }

    // Registrar flows como skills de orquestração
    const skillsRegistered: string[] = []
    for (const [flowName, flowJson] of Object.entries(flows)) {
      const skillId = `skill_${at.agent_type_id}_${flowName}_v1`
      const res = await fetch(`${opts.registryUrl}/v1/skills`, {
        method:  "POST",
        headers: { "Content-Type": "application/json", "x-tenant-id": opts.tenantId, "x-api-key": opts.apiKey },
        body: JSON.stringify({
          skill_id:       skillId,
          name:           `${at.agent_type_id} — ${flowName}`,
          version:        "1.0.0",
          description:    `Flow ${flowName} importado do repositório GitAgent`,
          classification: { type: "orchestrator" },
          instruction:    { prompt_id: `prompt_${at.agent_type_id}_v1` },
          flow:           flowJson,
        }),
      })
      if (res.ok || res.status === 409) {
        skillsRegistered.push(skillId)
      } else {
        errors.push(`Skill ${skillId}: ${res.status}`)
      }
    }

    return {
      agent_type_id:        at.agent_type_id,
      skills_registered:    skillsRegistered,
      flows_registered:     Object.keys(flows),
      certification_status: errors.length === 0 ? "passed" : "failed",
      imported_at:          now,
      errors:               errors.length > 0 ? errors : undefined,
    }
  }

  private async _clone(url: string, branch: string): Promise<string> {
    const tmp = mkdtempSync(join(tmpdir(), "plughub-import-"))
    execSync(`git clone --depth 1 --branch ${branch} ${url} ${tmp}`, { stdio: "pipe" })
    return tmp
  }
}
