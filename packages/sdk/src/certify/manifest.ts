/**
 * certify/manifest.ts
 * Lê e valida o manifesto agent.yaml do repositório GitAgent.
 * Spec: PlugHub v24.0 seção 4.6e
 */

import * as fs   from "node:fs"
import * as path from "node:path"
import { z }     from "zod"
import { parseYaml } from "./yaml"

// ─────────────────────────────────────────────
// Schema do manifesto
// ─────────────────────────────────────────────

export const AgentManifestSchema = z.object({
  agent_type_id:          z.string().regex(/^[a-z][a-z0-9_]+_v\d+$/,
                          "Formato esperado: {nome}_v{n} — ex: agente_retencao_v1"),
  framework:              z.string().min(1, "framework é obrigatório"),
  execution_model:        z.enum(["stateless", "stateful"],
                          { errorMap: () => ({ message: "execution_model deve ser 'stateless' ou 'stateful'" }) }),
  pools:                  z.array(z.string().min(1)).min(1, "pelo menos um pool é obrigatório"),
  permissions:            z.array(z.string()).default([]),
  version:                z.string().optional(),
  description:            z.string().optional(),
  max_concurrent_sessions: z.number().int().min(1).optional(),
  skills:                 z.array(z.object({
                            skill_id:       z.string(),
                            version_policy: z.string().optional(),
                            exact_version:  z.string().optional(),
                          })).optional(),
  classification:         z.object({
                            type:     z.string().optional(),
                            industry: z.string().optional(),
                            domain:   z.string().optional(),
                          }).optional(),
  profile:                z.record(z.number()).optional(),
})

export type AgentManifest = z.infer<typeof AgentManifestSchema>

export interface ManifestResult {
  manifest:  AgentManifest | null
  filePath:  string | null
  errors:    string[]
}

// ─────────────────────────────────────────────
// loadManifest
// ─────────────────────────────────────────────

const MANIFEST_CANDIDATES = [
  "agent.yaml", "agent.yml",
  "manifest.yaml", "manifest.yml",
]

export function loadManifest(dirPath: string): ManifestResult {
  for (const candidate of MANIFEST_CANDIDATES) {
    const filePath = path.join(dirPath, candidate)
    if (!fs.existsSync(filePath)) continue

    let raw: string
    try {
      raw = fs.readFileSync(filePath, "utf-8")
    } catch (e) {
      return {
        manifest: null, filePath,
        errors: [`${candidate}: erro ao ler arquivo — ${String(e)}`],
      }
    }

    let parsed: unknown
    try {
      parsed = parseYaml(raw)
    } catch (e) {
      return {
        manifest: null, filePath,
        errors: [`${candidate}: YAML inválido — ${String(e)}`],
      }
    }

    const result = AgentManifestSchema.safeParse(parsed)
    if (result.success) {
      return { manifest: result.data, filePath, errors: [] }
    }

    const errors = result.error.errors.map(e => {
      const field = e.path.join(".") || "raiz"
      return `${candidate}: campo '${field}' — ${e.message}`
    })
    return { manifest: null, filePath, errors }
  }

  return {
    manifest: null, filePath: null,
    errors: [
      "agent.yaml não encontrado. Crie o manifesto com os campos: " +
      "agent_type_id, framework, execution_model, pools, permissions",
    ],
  }
}
