/**
 * regenerate/convert.ts
 * Converte artefatos GitAgent para formato nativo PlugHub.
 * Spec: PlugHub v24.0 seção 4.6i
 *
 * Saída em ./output/:
 *   agent-type.json   sempre presente
 *   prompt.md         sempre presente
 *   skill-ref.json    quando SKILL.md existir
 *   flow.json         quando flow.yaml existir e for válido
 */

import * as fs   from "node:fs"
import * as path from "node:path"
import type { GitAgentArtifacts } from "./reader"
import type { ParsedStep }        from "../certify/flow"

// ─────────────────────────────────────────────
// Tipos de saída
// ─────────────────────────────────────────────

export interface ConvertResult {
  outputPath:  string
  files:       string[]
  warnings:    string[]
}

export interface AgentTypeJson {
  agent_type_id:   string
  framework:       string
  execution_model: string
  pools:           string[]
  permissions:     string[]
  version:         string
  description?:    string
  _generated_from: string
  _generated_at:   string
}

// ─────────────────────────────────────────────
// convertGitAgent
// ─────────────────────────────────────────────

export function convertGitAgent(
  artifacts:  GitAgentArtifacts,
  outputPath: string,
): ConvertResult {
  const files:    string[] = []
  const warnings: string[] = []

  // Garantir diretório de saída
  fs.mkdirSync(outputPath, { recursive: true })

  const manifest = artifacts.manifest
  if (!manifest) {
    throw new Error(
      "Manifesto inválido — corrija o agent.yaml antes de regenerar:\n" +
      artifacts.manifestErrors.join("\n")
    )
  }

  // Mesclar permissions do manifesto com as do tools/
  const allPermissions = [
    ...manifest.permissions,
    ...artifacts.tools.filter(t => !manifest.permissions.includes(t)),
  ]

  // ── 1. agent-type.json ──────────────────────────────────────────────────
  const agentType: AgentTypeJson = {
    agent_type_id:   manifest.agent_type_id,
    framework:       manifest.framework,
    execution_model: manifest.execution_model,
    pools:           manifest.pools,
    permissions:     allPermissions,
    version:         artifacts.version,
    ...(manifest.description ? { description: manifest.description } : {}),
    _generated_from: "plughub-sdk regenerate",
    _generated_at:   new Date().toISOString(),
  }

  const agentTypePath = path.join(outputPath, "agent-type.json")
  fs.writeFileSync(agentTypePath, JSON.stringify(agentType, null, 2))
  files.push("agent-type.json")

  // ── 2. prompt.md ────────────────────────────────────────────────────────
  const promptContent = buildPromptMd(artifacts.soul, artifacts.duties, manifest.agent_type_id)
  const promptPath = path.join(outputPath, "prompt.md")
  fs.writeFileSync(promptPath, promptContent)
  files.push("prompt.md")

  if (!artifacts.soul) {
    warnings.push("SOUL.md não encontrado — prompt.md Camada 1 gerada com placeholder. Revise o arquivo.")
  }
  if (!artifacts.duties) {
    warnings.push("DUTIES.md não encontrado — prompt.md Camada 2 gerada com placeholder. Revise o arquivo.")
  }

  // ── 3. skill-ref.json (quando SKILL.md existir) ─────────────────────────
  if (artifacts.skill) {
    const skillRef = buildSkillRef(artifacts.skill, manifest.agent_type_id)
    const skillRefPath = path.join(outputPath, "skill-ref.json")
    fs.writeFileSync(skillRefPath, JSON.stringify(skillRef, null, 2))
    files.push("skill-ref.json")
  }

  // ── 4. flow.json (quando flow.yaml existir e for válido) ─────────────────
  if (artifacts.flow) {
    const flowJson = convertFlowToNative(artifacts.flow)
    const flowPath = path.join(outputPath, "flow.json")
    fs.writeFileSync(flowPath, JSON.stringify(flowJson, null, 2))
    files.push("flow.json")
  } else if (artifacts.flowErrors && artifacts.flowErrors.length > 0) {
    // flow.yaml existia mas era inválido — regenerate falha (spec)
    throw new Error(
      `flow.yaml inválido — regenerate abortado:\n` +
      artifacts.flowErrors.map(e =>
        e.step_id ? `  [step: ${e.step_id}] ${e.message}` : `  ${e.message}`
      ).join("\n")
    )
  }

  // ── 5. proxy_config.yaml (quando permissions[] declarar MCP Servers) ───────
  const mcpServers = extractMcpServers(allPermissions)
  if (mcpServers.length > 0) {
    const proxyConfigContent = buildProxyConfigYaml(mcpServers)
    const proxyConfigPath    = path.join(outputPath, "proxy_config.yaml")
    fs.writeFileSync(proxyConfigPath, proxyConfigContent)
    files.push("proxy_config.yaml")
  }

  return { outputPath, files, warnings }
}

// ─────────────────────────────────────────────
// Builders
// ─────────────────────────────────────────────

function buildPromptMd(soul?: string, duties?: string, agentTypeId?: string): string {
  const layers: string[] = []

  layers.push("# Prompt do Agente")
  layers.push(`<!-- Gerado por plughub-sdk regenerate — revise antes do deploy -->`)
  layers.push("")

  // Camada 1 — Identity & Persona (SOUL.md)
  layers.push("## Camada 1 — Identity & Persona")
  layers.push("")
  if (soul) {
    layers.push(soul.trim())
  } else {
    layers.push(`<!-- TODO: Defina a identidade e persona do agente ${agentTypeId ?? ""} -->`)
    layers.push(`Você é um agente especializado. Seu objetivo é ajudar os clientes de forma`)
    layers.push(`eficiente e respeitosa, seguindo as políticas da empresa.`)
  }
  layers.push("")

  // Camada 2 — Políticas e limites (DUTIES.md)
  layers.push("## Camada 2 — Políticas e Limites")
  layers.push("")
  if (duties) {
    layers.push(duties.trim())
  } else {
    layers.push(`<!-- TODO: Defina as políticas e limites do agente ${agentTypeId ?? ""} -->`)
    layers.push(`Sempre siga as políticas da empresa. Nunca compartilhe informações confidenciais.`)
    layers.push(`Encaminhe para humano quando não souber resolver.`)
  }
  layers.push("")

  return layers.join("\n")
}

function buildSkillRef(skillMd: string, agentTypeId: string): Record<string, unknown> {
  // Extrair skill_id de SKILL.md se disponível
  const skillIdMatch = skillMd.match(/skill_id[:\s]+['"`]?([a-z][a-z0-9_]+_v\d+)['"`]?/)
  const skillId = skillIdMatch?.[1] ??
    `skill_${agentTypeId.replace(/^agente_/, "").replace(/_v\d+$/, "")}_v1`

  // Extrair version_policy se declarada
  const policyMatch = skillMd.match(/version_policy[:\s]+['"`]?(stable|latest|exact)['"`]?/)
  const versionPolicy = policyMatch?.[1] ?? "stable"

  return {
    skill_id:       skillId,
    version_policy: versionPolicy,
    _source:        "SKILL.md",
    _generated_at:  new Date().toISOString(),
  }
}

/**
 * Converte um ParsedFlow (formato GitAgent/YAML) para o formato nativo JSON.
 *
 * Diferenças principais:
 *   task.agent_pool → task.target.skill_id (inferindo skill_id do pool name)
 */
function convertFlowToNative(flow: { entry: string; steps: ParsedStep[] }): Record<string, unknown> {
  const nativeSteps = flow.steps.map(step => convertStep(step))
  return {
    entry: flow.entry,
    steps: nativeSteps,
  }
}

// ─────────────────────────────────────────────
// proxy_config.yaml helpers
// ─────────────────────────────────────────────

/**
 * Extracts unique MCP server names from permissions[].
 * Format: "mcp-server-crm:customer_get" → "mcp-server-crm"
 * Returns sorted unique list of server names.
 */
export function extractMcpServers(permissions: string[]): string[] {
  const servers = new Set<string>()
  for (const perm of permissions) {
    const colonIdx = perm.indexOf(":")
    if (colonIdx > 0) {
      const server = perm.slice(0, colonIdx).trim()
      if (server.startsWith("mcp-server-")) servers.add(server)
    }
  }
  return [...servers].sort()
}

/**
 * Derives the env-var name for a given MCP server.
 * mcp-server-crm   → MCP_CRM_URL
 * mcp-server-telco → MCP_TELCO_URL
 */
export function serverToEnvVar(serverName: string): string {
  const suffix = serverName
    .replace(/^mcp-server-/, "")
    .toUpperCase()
    .replace(/-/g, "_")
  return `MCP_${suffix}_URL`
}

/**
 * Builds the full proxy_config.yaml content string.
 * spec 4.6k / CLAUDE.md proxy section
 */
export function buildProxyConfigYaml(mcpServers: string[]): string {
  const routeLines = mcpServers
    .map(s => `  ${s}: \${${serverToEnvVar(s)}}`)
    .join("\n")

  return [
    `port: 7422`,
    `session_token_env: PLUGHUB_SESSION_TOKEN`,
    `audit_buffer_size: 1000`,
    `audit_flush_interval_ms: 500`,
    `circuit_breaker:`,
    `  timeout_ms: 50`,
    `  mode_on_failure: error_clear`,
    `routes:`,
    routeLines,
    ``,
  ].join("\n")
}

function convertStep(step: ParsedStep): Record<string, unknown> {
  const base: Record<string, unknown> = {
    id:   step.id,
    type: step.type,
  }

  switch (step.type) {
    case "task": {
      // GitAgent: agent_pool → native: target.skill_id
      const target = step["target"] as Record<string, unknown> | undefined
      const agentPool = step["agent_pool"] as string | undefined
      base["target"] = target ?? {
        skill_id: agentPool
          ? `skill_${agentPool.replace(/[^a-z0-9]/g, "_")}_v1`
          : "skill_unknown_v1",
      }
      if (!target && agentPool) {
        // Preservar agent_pool como metadata
        base["_agent_pool"] = agentPool
      }
      base["execution_mode"] = step["execution_mode"] ?? "sync"
      base["on_success"] = step["on_success"]
      base["on_failure"] = step["on_failure"]
      break
    }

    case "choice":
      base["conditions"] = step["conditions"]
      base["default"]    = step["default"]
      break

    case "catch":
      base["error_context"] = step["error_context"]
      base["strategies"]    = step["strategies"]
      base["on_failure"]    = step["on_failure"]
      break

    case "escalate":
      base["target"]       = step["target"]
      base["context"]      = step["context"] ?? "pipeline_state"
      if (step["error_reason"]) base["error_reason"] = step["error_reason"]
      break

    case "complete":
      base["outcome"] = step["outcome"]
      break

    case "invoke":
      base["target"]     = step["target"]
      base["input"]      = step["input"]
      base["output_as"]  = step["output_as"]
      base["on_success"] = step["on_success"]
      base["on_failure"] = step["on_failure"]
      break

    case "reason":
      base["prompt_id"]          = step["prompt_id"]
      base["input"]              = step["input"]
      base["output_schema"]      = step["output_schema"]
      base["output_as"]          = step["output_as"]
      base["max_format_retries"] = step["max_format_retries"] ?? 1
      base["on_success"]         = step["on_success"]
      base["on_failure"]         = step["on_failure"]
      break

    case "notify":
      base["message"]    = step["message"]
      base["channel"]    = step["channel"] ?? "session"
      base["on_success"] = step["on_success"]
      base["on_failure"] = step["on_failure"]
      break

    default:
      // Preservar campos desconhecidos
      Object.assign(base, step)
      break
  }

  return base
}
