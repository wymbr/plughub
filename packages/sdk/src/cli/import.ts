/**
 * cli/import.ts
 * plughub-sdk import [path] — registra agente nativo no Agent Registry.
 * Spec: PlugHub v24.0 seção 4.6e / 4.5 / 4.7
 *
 * Uso:
 *   plughub-sdk import
 *   plughub-sdk import ./meu-agente/
 *
 * Lê .plughub/config.yaml para registry_url e tenant_id.
 * Para agentes plughub-native:
 *   1. Registra skill (com flow embutido) em POST /v1/skills
 *   2. Registra agent-type         em POST /v1/agent-types
 */

import { Command }  from "commander"
import * as fs      from "node:fs"
import * as path    from "node:path"
import * as https   from "node:https"
import * as http    from "node:http"
import { parseYaml }            from "../certify/yaml"
import { loadManifest }         from "../certify/manifest"
import type { AgentManifest }   from "../certify/manifest"
import { findAndValidateFlow }  from "../certify/flow"
import type { ParsedFlow }      from "../certify/flow"

// ─────────────────────────────────────────────
// Tipos
// ─────────────────────────────────────────────

interface PlughubConfig {
  registry_url: string
  tenant_id:    string
}

interface ImportResult {
  agent_type_id:  string
  skill_id:       string
  skills_created: string[]
  agent_created:  boolean
  imported_at:    string
}

// ─────────────────────────────────────────────
// Command registration
// ─────────────────────────────────────────────

export function registerImportCommand(program: Command): void {
  program
    .command("import [dir]")
    .description("Registra agente nativo no Agent Registry (spec 4.5 / 4.7)")
    .option("--json", "Saída em formato JSON")
    .option("--registry-url <url>", "URL do Agent Registry (sobrescreve .plughub/config.yaml)")
    .option("--tenant-id <id>",     "Tenant ID (sobrescreve .plughub/config.yaml)")
    .action(async (dir: string | undefined, opts: { json?: boolean; registryUrl?: string; tenantId?: string }) => {
      const dirPath = path.resolve(dir ?? ".")

      try {
        const result = await importAgent(dirPath, {
          registryUrl: opts.registryUrl,
          tenantId:    opts.tenantId,
        })

        if (opts.json) {
          console.log(JSON.stringify(result, null, 2))
        } else {
          _printResult(result)
        }
        process.exit(0)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        if (opts.json) {
          console.log(JSON.stringify({ error: msg }, null, 2))
        } else {
          console.error(`\n❌ Import falhou: ${msg}`)
        }
        process.exit(1)
      }
    })
}

// ─────────────────────────────────────────────
// importAgent
// ─────────────────────────────────────────────

export async function importAgent(
  dirPath: string,
  overrides: { registryUrl?: string; tenantId?: string } = {},
): Promise<ImportResult> {
  // ── 1. Ler configuração ──
  const config    = loadPlughubConfig(dirPath)
  const regUrl    = overrides.registryUrl ?? config.registry_url
  const tenantId  = overrides.tenantId    ?? config.tenant_id

  if (!regUrl)   throw new Error("registry_url não encontrado — configure .plughub/config.yaml ou use --registry-url")
  if (!tenantId) throw new Error("tenant_id não encontrado — configure .plughub/config.yaml ou use --tenant-id")

  // ── 2. Carregar manifesto ──
  const { manifest, errors: manifestErrors } = loadManifest(dirPath)
  if (!manifest) {
    throw new Error(`Manifesto inválido:\n${manifestErrors.join("\n")}`)
  }

  // ── 3. Carregar flow (se existir) ──
  const flowResult = findAndValidateFlow(dirPath)
  if (flowResult.filePath && !flowResult.valid) {
    throw new Error(`flow inválido:\n${flowResult.errors.map(e => e.message).join("\n")}`)
  }

  const skillsCreated: string[] = []

  // ── 4. Registrar cada skill declarada no manifesto ──
  for (const skillRef of manifest.skills ?? []) {
    const skillId = (skillRef as Record<string, unknown>)["skill_id"] as string
    if (!skillId) continue

    const flow: ParsedFlow | null = flowResult.flow ?? null
    const skillPayload = _buildSkillPayload(skillId, manifest, flow)
    const skillResp = await _post(`${regUrl}/v1/skills`, tenantId, skillPayload)

    if (skillResp.status !== 201) {
      // 409 = já existe — aceitar como idempotente
      if (skillResp.status !== 409) {
        throw new Error(
          `POST /v1/skills retornou ${skillResp.status}: ${JSON.stringify(skillResp.body)}`
        )
      }
    } else {
      skillsCreated.push(skillId)
    }
  }

  // ── 5. Registrar agent-type ──
  const agentPayload = _buildAgentTypePayload(manifest)
  const agentResp = await _post(`${regUrl}/v1/agent-types`, tenantId, agentPayload)

  if (agentResp.status !== 201) {
    if (agentResp.status === 409) {
      console.warn(`  ⚠ agent-type já registrado — ignorando (use uma nova versão para atualizar)`)
    } else {
      throw new Error(
        `POST /v1/agent-types retornou ${agentResp.status}: ${JSON.stringify(agentResp.body)}`
      )
    }
  }

  const primarySkillId = ((manifest.skills ?? [])[0] as Record<string, unknown> | undefined)?.["skill_id"] as string ?? ""

  return {
    agent_type_id:  manifest.agent_type_id,
    skill_id:       primarySkillId,
    skills_created: skillsCreated,
    agent_created:  agentResp.status === 201,
    imported_at:    new Date().toISOString(),
  }
}

// ─────────────────────────────────────────────
// Payload builders
// ─────────────────────────────────────────────

function _buildSkillPayload(
  skillId:  string,
  manifest: AgentManifest,
  flow:     ParsedFlow | null,
): Record<string, unknown> {
  const isOrchestrator = flow !== null

  // Derivar nome legível a partir do skill_id (ex: skill_retencao_oferta_v1 → Retenção Oferta)
  const nameParts = skillId
    .replace(/^skill_/, "")
    .replace(/_v\d+$/, "")
    .split("_")
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
  const name = nameParts.join(" ")

  const classification: Record<string, unknown> = {
    type: isOrchestrator ? "orchestrator" : "vertical",
  }
  if (manifest.classification) {
    const c = manifest.classification as Record<string, unknown>
    if (c["industry"]) classification["vertical"] = c["industry"]
    if (c["domain"])   classification["domain"]   = c["domain"]
  }

  const payload: Record<string, unknown> = {
    skill_id:    skillId,
    name,
    version:     manifest.version ? manifest.version.replace(/\.\d+$/, "") : "1.0",
    description: `Skill ${name} — importada de ${manifest.agent_type_id}`,
    classification,
    instruction: { prompt_id: `prompt_${skillId}` },
    knowledge_domains: [],
  }

  if (isOrchestrator && flow) {
    payload["flow"] = flow
  }

  return payload
}

function _buildAgentTypePayload(manifest: AgentManifest): Record<string, unknown> {
  const skills = (manifest.skills ?? []) as Array<Record<string, unknown>>
  const hasFlow = skills.length > 0

  return {
    agent_type_id:           manifest.agent_type_id,
    framework:               manifest.framework,
    execution_model:         manifest.execution_model,
    role:                    hasFlow ? "orchestrator" : "executor",
    max_concurrent_sessions: manifest.max_concurrent_sessions ?? 1,
    pools:                   manifest.pools,
    skills,
    permissions:             manifest.permissions ?? [],
    capabilities:            {},
    ...(manifest.classification
      ? { agent_classification: manifest.classification }
      : {}),
  }
}

// ─────────────────────────────────────────────
// Config loader
// ─────────────────────────────────────────────

function loadPlughubConfig(dirPath: string): Partial<PlughubConfig> {
  const configPath = path.join(dirPath, ".plughub", "config.yaml")
  if (!fs.existsSync(configPath)) return {}

  try {
    const raw = fs.readFileSync(configPath, "utf-8")
    const parsed = parseYaml(raw) as Record<string, unknown>
    return {
      registry_url: parsed["registry_url"] as string | undefined,
      tenant_id:    parsed["tenant_id"]    as string | undefined,
    }
  } catch {
    return {}
  }
}

// ─────────────────────────────────────────────
// HTTP helper
// ─────────────────────────────────────────────

async function _post(
  url:      string,
  tenantId: string,
  body:     unknown,
): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const payload  = JSON.stringify(body)
    const parsed   = new URL(url)
    const isHttps  = parsed.protocol === "https:"
    const lib      = isHttps ? https : http

    const options: http.RequestOptions = {
      hostname: parsed.hostname,
      port:     parsed.port || (isHttps ? 443 : 80),
      path:     parsed.pathname + (parsed.search ?? ""),
      method:   "POST",
      headers:  {
        "Content-Type":   "application/json",
        "Content-Length": Buffer.byteLength(payload),
        "x-tenant-id":    tenantId,
        "x-user-id":      "plughub-sdk",
      },
    }

    const req = lib.request(options, res => {
      let data = ""
      res.on("data", chunk => { data += chunk })
      res.on("end", () => {
        let parsed: unknown
        try { parsed = JSON.parse(data) } catch { parsed = data }
        resolve({ status: res.statusCode ?? 0, body: parsed })
      })
    })

    req.on("error", reject)
    req.write(payload)
    req.end()
  })
}

// ─────────────────────────────────────────────
// Output
// ─────────────────────────────────────────────

function _printResult(result: ImportResult): void {
  console.log(`\n✅ IMPORTED — ${result.agent_type_id}`)
  console.log(`   skill_id:      ${result.skill_id}`)
  console.log(`   skills_created: [${result.skills_created.join(", ")}]`)
  console.log(`   agent_created:  ${result.agent_created}`)
  console.log(`   Em:            ${result.imported_at}\n`)
}
