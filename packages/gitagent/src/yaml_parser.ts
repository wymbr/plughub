/**
 * yaml_parser.ts
 * Converte repositório GitAgent (YAML) para payloads JSON da plataforma.
 * Spec: PlugHub v24.0 seção 4.9
 */

import { readFileSync, existsSync, readdirSync } from "fs"
import { join }  from "path"
import yaml      from "js-yaml"
import { SkillFlowSchema, AgentTypeRegistrationSchema } from "@plughub/schemas"
import type { AgentTypeRegistration, SkillFlow }        from "@plughub/schemas"

export interface AgentManifest {
  agent_type_id:            string
  framework:                string
  execution_model:          "stateless" | "stateful"
  max_concurrent_sessions?: number
  pools:                    string[]
  skills?:                  Array<{ skill_id: string; version_policy: string }>
  permissions?:             string[]
  classification?:          Record<string, string>
  profile?:                 Record<string, number>
}

export interface ParsedRepository {
  manifest:     AgentManifest
  instructions: string
  flows:        Record<string, SkillFlow>
  tools?:       unknown
  schema?:      unknown
  evals?:       unknown
}

export class GitAgentParser {

  parse(repoPath: string): ParsedRepository {
    this._assertExists(repoPath, "agent.yaml")
    this._assertExists(repoPath, "instructions.md")

    return {
      manifest:     this._parseManifest(repoPath),
      instructions: readFileSync(join(repoPath, "instructions.md"), "utf-8"),
      flows:        this._parseFlows(repoPath),
      tools:        this._parseOptional(repoPath, "tools.yaml"),
      schema:       this._parseOptional(repoPath, "schema.yaml"),
      evals:        this._parseOptional(repoPath, "evals/criteria.yaml"),
    }
  }

  toAgentTypeRegistration(parsed: ParsedRepository): AgentTypeRegistration {
    const { manifest } = parsed
    return AgentTypeRegistrationSchema.parse({
      agent_type_id:           manifest.agent_type_id,
      framework:               manifest.framework,
      execution_model:         manifest.execution_model,
      max_concurrent_sessions: manifest.max_concurrent_sessions ?? 1,
      pools:                   manifest.pools,
      skills:                  manifest.skills ?? [],
      permissions:             manifest.permissions ?? [],
      capabilities:            {},
      agent_classification:    manifest.classification,
      profile:                 manifest.profile ?? {},
    })
  }

  getFlowsAsJson(parsed: ParsedRepository): Record<string, object> {
    return Object.fromEntries(
      Object.entries(parsed.flows).map(([name, flow]) => [name, flow as object])
    )
  }

  private _parseManifest(repoPath: string): AgentManifest {
    return yaml.load(readFileSync(join(repoPath, "agent.yaml"), "utf-8")) as AgentManifest
  }

  private _parseFlows(repoPath: string): Record<string, SkillFlow> {
    const flowsDir = join(repoPath, "flows")
    if (!existsSync(flowsDir)) return {}

    const flows: Record<string, SkillFlow> = {}
    const files = readdirSync(flowsDir).filter(f => /\.ya?ml$/.test(f))

    for (const file of files) {
      const raw  = readFileSync(join(flowsDir, file), "utf-8")
      const name = file.replace(/\.ya?ml$/, "")
      // Valida YAML contra schema Zod — garante que o flow é válido antes de registrar
      flows[name] = SkillFlowSchema.parse(yaml.load(raw))
    }
    return flows
  }

  private _parseOptional(repoPath: string, relativePath: string): unknown {
    const fullPath = join(repoPath, relativePath)
    return existsSync(fullPath)
      ? yaml.load(readFileSync(fullPath, "utf-8"))
      : undefined
  }

  private _assertExists(repoPath: string, relativePath: string): void {
    if (!existsSync(join(repoPath, relativePath))) {
      throw new Error(
        `Repositório GitAgent inválido — arquivo obrigatório ausente: ${relativePath}`
      )
    }
  }
}
