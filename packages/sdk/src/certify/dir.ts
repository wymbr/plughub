/**
 * certify/dir.ts
 * Orquestra a certificação de um repositório GitAgent ou diretório de agente.
 * Spec: PlugHub v24.0 seção 4.6e
 *
 * Uso:
 *   plughub-sdk certify [path]
 *
 * path pode ser um diretório local ou repositório GitAgent.
 * Se omitido, usa o diretório atual.
 */

import * as fs from "node:fs"
import * as path from "node:path"
import { loadManifest }              from "./manifest"
import { checkLifecycleEvents,
         checkAgentDoneContract,
         checkPermissions }          from "./lifecycle"
import { findAndValidateFlow }       from "./flow"
import { parseYaml }                 from "./yaml"

// ─────────────────────────────────────────────
// Tipos de relatório
// ─────────────────────────────────────────────

export type CheckStatus = "passed" | "failed" | "warning" | "skipped"

export interface CertifyCheckItem {
  name:     string
  status:   CheckStatus
  message:  string
  detail?:  string
  errors?:  Array<{ file?: string; line?: number; message: string }>
}

export interface DirCertifyReport {
  agent_type_id: string
  version?:      string
  path:          string
  status:        "certified" | "failed"
  certified_at:  string
  checks:        CertifyCheckItem[]
}

// ─────────────────────────────────────────────
// certifyDir
// ─────────────────────────────────────────────

export function certifyDir(dirPath: string): DirCertifyReport {
  const checks: CertifyCheckItem[] = []
  const now = new Date().toISOString()
  let agentTypeId = path_basename(dirPath)
  let version: string | undefined

  // ── 1. Manifesto ──────────────────────────────────────────────────────────
  const { manifest, errors: manifestErrors } = loadManifest(dirPath)

  if (!manifest) {
    checks.push({
      name:    "manifest.present",
      status:  "failed",
      message: "Manifesto não encontrado ou inválido",
      errors:  manifestErrors.map(m => ({ message: m })),
    })
    return {
      agent_type_id: agentTypeId,
      path:          dirPath,
      status:        "failed",
      certified_at:  now,
      checks,
    }
  }

  agentTypeId = manifest.agent_type_id
  version     = manifest.version

  // Native orchestrator agents (plughub-native) have no agent code — the lifecycle
  // is managed by the platform runtime and execution logic lives in YAML flows.
  // Source-code analysis checks (lifecycle, contract, permissions) do not apply.
  const isNativeAgent = manifest.framework === "plughub-native" || manifest.framework === "native"

  checks.push({
    name:    "manifest.fields",
    status:  "passed",
    message: `Manifesto válido — ${manifest.agent_type_id} (${manifest.framework}, ${manifest.execution_model})`,
    detail:  `pools: ${manifest.pools.join(", ")} | permissions: ${manifest.permissions.length}`,
  })

  // ── 2. Ciclo de vida ──────────────────────────────────────────────────────
  if (isNativeAgent) {
    checks.push({
      name:    "lifecycle.events",
      status:  "passed",
      message: "Agente nativo — ciclo de vida gerenciado pelo runtime da plataforma (spec 4.2)",
    })
  } else {
    const lifecycle = checkLifecycleEvents(dirPath)

    if (lifecycle.missing.length === 0) {
      checks.push({
        name:    "lifecycle.events",
        status:  "passed",
        message: "Todos os 6 eventos do ciclo de vida implementados",
        detail:  Object.entries(lifecycle.found)
          .map(([e, loc]) => `${e} (${loc.file}:${loc.line})`)
          .join(", "),
      })
    } else {
      checks.push({
        name:    "lifecycle.events",
        status:  "failed",
        message: `${lifecycle.missing.length} evento(s) do ciclo de vida não encontrado(s)`,
        detail:  `Faltando: ${lifecycle.missing.join(", ")}`,
        errors:  lifecycle.missing.map(e => ({
          message: `Evento '${e}' não implementado — obrigatório pelo contrato de execução (spec 4.2)`,
        })),
      })
    }
  }

  // ── 3. Contrato agent_done ────────────────────────────────────────────────
  if (isNativeAgent) {
    checks.push({
      name:    "contract.issue_status",
      status:  "passed",
      message: "Agente nativo — contrato agent_done gerenciado pelo runtime da plataforma",
    })
    checks.push({
      name:    "contract.handoff_reason",
      status:  "passed",
      message: "Agente nativo — contrato agent_done gerenciado pelo runtime da plataforma",
    })
  } else {
    const doneCt = checkAgentDoneContract(dirPath)

    if (doneCt.missingIssueStatus.length === 0) {
      checks.push({
        name:    "contract.issue_status",
        status:  "passed",
        message: "issue_status presente em todas as ocorrências de agent_done",
      })
    } else {
      checks.push({
        name:    "contract.issue_status",
        status:  "failed",
        message: `issue_status ausente em ${doneCt.missingIssueStatus.length} chamada(s) de agent_done`,
        detail:  "issue_status é obrigatório e nunca vazio (spec 4.2)",
        errors:  doneCt.missingIssueStatus.map(loc => ({
          file:    loc.file,
          line:    loc.line,
          message: "agent_done sem issue_status",
        })),
      })
    }

    if (doneCt.missingHandoffReason.length === 0) {
      checks.push({
        name:    "contract.handoff_reason",
        status:  "passed",
        message: "handoff_reason presente quando outcome !== 'resolved'",
      })
    } else {
      checks.push({
        name:    "contract.handoff_reason",
        status:  "failed",
        message: `handoff_reason ausente em ${doneCt.missingHandoffReason.length} agent_done com outcome não-resolved`,
        detail:  "handoff_reason é obrigatório quando outcome !== 'resolved' (spec 4.2)",
        errors:  doneCt.missingHandoffReason.map(loc => ({
          file:    loc.file,
          line:    loc.line,
          message: "agent_done com outcome não-resolved sem handoff_reason",
        })),
      })
    }
  }

  // ── 4. Permissões MCP ─────────────────────────────────────────────────────
  if (isNativeAgent) {
    checks.push({
      name:    "permissions.mcp_tools",
      status:  "passed",
      message: "Agente nativo — chamadas MCP gerenciadas pelo runtime da plataforma via PlugHubAdapter",
    })
    checks.push({
      name:    "permissions.no_direct_backend",
      status:  "passed",
      message: "Agente nativo — acesso a backends exclusivamente via PlugHubAdapter (spec 4.2)",
    })
  } else {
    const perms = checkPermissions(dirPath, manifest.permissions)

    if (perms.undeclaredTools.length === 0) {
      checks.push({
        name:    "permissions.mcp_tools",
        status:  "passed",
        message: "Todas as tools MCP chamadas estão declaradas em permissions[]",
      })
    } else {
      const unique = [...new Set(perms.undeclaredTools.map(t => t.tool))]
      checks.push({
        name:    "permissions.mcp_tools",
        status:  "failed",
        message: `${perms.undeclaredTools.length} chamada(s) a tools não declaradas em permissions[]`,
        detail:  `Tools: ${unique.join(", ")}`,
        errors:  perms.undeclaredTools.map(t => ({
          file:    t.file,
          line:    t.line,
          message: `Tool '${t.tool}' chamada mas não declarada em permissions[]`,
        })),
      })
    }

    if (perms.directBackendCalls.length === 0) {
      checks.push({
        name:    "permissions.no_direct_backend",
        status:  "passed",
        message: "Nenhuma chamada direta a backend detectada",
      })
    } else {
      checks.push({
        name:    "permissions.no_direct_backend",
        status:  "failed",
        message: `${perms.directBackendCalls.length} chamada(s) direta(s) a backend detectada(s)`,
        detail:  "Agentes devem acessar backends exclusivamente via MCP Servers autorizados (spec 4.2)",
        errors:  perms.directBackendCalls.map(c => ({
          file:    c.file,
          line:    c.line,
          message: c.detail,
        })),
      })
    }
  }

  // ── 5. Flow (opcional) ────────────────────────────────────────────────────
  const flowResult = findAndValidateFlow(dirPath)

  if (flowResult.filePath) {
    if (flowResult.valid) {
      const stepCount = flowResult.flow?.steps.length ?? 0
      checks.push({
        name:    "flow.valid",
        status:  "passed",
        message: `flow.yaml válido — ${stepCount} steps, entry: '${flowResult.flow?.entry}'`,
      })
    } else {
      checks.push({
        name:    "flow.valid",
        status:  "failed",
        message: `flow.yaml inválido — ${flowResult.errors.length} erro(s)`,
        errors:  flowResult.errors.map(e => ({
          message: e.step_id
            ? `[step: ${e.step_id}${e.field ? `/${e.field}` : ""}] ${e.message}`
            : e.message,
        })),
      })
    }
  }
  // Se não encontrou flow.yaml, não é erro (opcional)

  // ── 6. Proxy sidecar (agentes externos — framework não nativo) ───────────
  // External agents must route all domain MCP calls through the proxy sidecar.
  // Native agents use in-process PlugHubAdapter and do not need the sidecar.
  // Spec: 4.6k / CLAUDE.md invariants
  if (!isNativeAgent) {
    checks.push(checkProxyConfig(dirPath, manifest.permissions))
  }

  // ── Resultado final ───────────────────────────────────────────────────────
  const hasFailed = checks.some(c => c.status === "failed")

  return {
    agent_type_id: agentTypeId,
    version,
    path:          dirPath,
    status:        hasFailed ? "failed" : "certified",
    certified_at:  now,
    checks,
  }
}

function path_basename(p: string): string {
  return p.replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? p
}

// ─────────────────────────────────────────────
// Proxy config check (spec 4.6k)
// ─────────────────────────────────────────────

function checkProxyConfig(
  dirPath:     string,
  permissions: string[],
): CertifyCheckItem {
  const proxyPath = path.join(dirPath, "proxy_config.yaml")

  if (!fs.existsSync(proxyPath)) {
    return {
      name:    "proxy.config_present",
      status:  "failed",
      message: "external agent missing proxy_config.yaml — all domain MCP calls must route through the proxy sidecar (spec 4.6k)",
      detail:  "Run: plughub-sdk regenerate to generate proxy_config.yaml, then start with: plughub-sdk proxy --config ./output/proxy_config.yaml",
    }
  }

  // Parse proxy_config.yaml
  let proxyConfig: Record<string, unknown>
  try {
    const raw    = fs.readFileSync(proxyPath, "utf-8")
    proxyConfig  = parseYaml(raw) as Record<string, unknown>
  } catch {
    return {
      name:    "proxy.config_present",
      status:  "failed",
      message: "proxy_config.yaml present but could not be parsed",
    }
  }

  // Check port declared
  if (!proxyConfig["port"]) {
    return {
      name:    "proxy.config_present",
      status:  "failed",
      message: "proxy_config.yaml missing required field: port",
    }
  }

  // Check all MCP servers in permissions[] have a route
  const mcpServers  = extractUniqueServers(permissions)
  const routes      = (proxyConfig["routes"] ?? {}) as Record<string, unknown>
  const missingRoutes = mcpServers.filter(s => !(s in routes))

  if (missingRoutes.length > 0) {
    return {
      name:    "proxy.config_present",
      status:  "failed",
      message: `proxy_config.yaml routes missing for: ${missingRoutes.join(", ")}`,
      detail:  `All MCP servers in permissions[] must have a corresponding entry in routes[]`,
      errors:  missingRoutes.map(s => ({
        message: `MCP server '${s}' declared in permissions[] but not in proxy_config.yaml routes`,
      })),
    }
  }

  return {
    name:    "proxy.config_present",
    status:  "passed",
    message: `proxy_config.yaml present — port: ${proxyConfig["port"]}, routes for ${mcpServers.length} MCP server(s)`,
    detail:  mcpServers.length > 0 ? `routes: ${mcpServers.join(", ")}` : "no MCP server permissions declared",
  }
}

function extractUniqueServers(permissions: string[]): string[] {
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
