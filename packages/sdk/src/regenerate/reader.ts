/**
 * regenerate/reader.ts
 * Lê os artefatos de um repositório GitAgent.
 * Spec: PlugHub v24.0 seção 4.6i
 *
 * Mapeamento de arquivos GitAgent:
 *   agent.yaml  → manifesto (agent_type_id, framework, pools, permissions)
 *   SOUL.md     → prompt.md Camada 1 (Identity & Persona)
 *   DUTIES.md   → prompt.md Camada 2 (Políticas e limites)
 *   SKILL.md    → skill-ref.json
 *   tools/      → permissions[] no agent-type.json
 *   git tag     → version no agent-type.json
 *   flow.yaml   → flow.json (quando presente e válido)
 */

import * as fs    from "node:fs"
import * as path  from "node:path"
import * as child from "node:child_process"
import { loadManifest } from "../certify/manifest"
import { findAndValidateFlow } from "../certify/flow"

// ─────────────────────────────────────────────
// Tipos
// ─────────────────────────────────────────────

export interface GitAgentArtifacts {
  /** Conteúdo do agent.yaml parseado */
  manifest:    import("../certify/manifest").AgentManifest | null
  manifestErrors: string[]
  /** Conteúdo de SOUL.md */
  soul?:       string
  /** Conteúdo de DUTIES.md */
  duties?:     string
  /** Conteúdo de SKILL.md */
  skill?:      string
  /** Tools encontradas em tools/ */
  tools:       string[]
  /** Versão inferida do git tag/branch */
  version:     string
  /** Flow validado (quando presente) */
  flow?:       import("../certify/flow").ParsedFlow
  flowErrors?: import("../certify/flow").FlowError[]
  flowPath?:   string
}

// ─────────────────────────────────────────────
// readGitAgent
// ─────────────────────────────────────────────

export function readGitAgent(repoPath: string): GitAgentArtifacts {
  const { manifest, errors: manifestErrors } = loadManifest(repoPath)

  const soul   = readOptional(repoPath, ["SOUL.md", "soul.md"])
  const duties = readOptional(repoPath, ["DUTIES.md", "duties.md"])
  const skill  = readOptional(repoPath, ["SKILL.md", "skill.md"])
  const tools  = readTools(repoPath)
  const version = inferVersion(repoPath, manifest?.version)

  // Carregar e validar flow (opcional)
  let flow:       import("../certify/flow").ParsedFlow | undefined
  let flowErrors: import("../certify/flow").FlowError[] | undefined
  let flowPath:   string | undefined

  const flowResult = findAndValidateFlow(repoPath)
  if (flowResult.filePath) {
    flowPath   = flowResult.filePath
    flow       = flowResult.flow
    flowErrors = flowResult.valid ? undefined : flowResult.errors
  }

  return {
    manifest,
    manifestErrors,
    soul,
    duties,
    skill,
    tools,
    version,
    flow,
    flowErrors,
    flowPath,
  }
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

function readOptional(dir: string, candidates: string[]): string | undefined {
  for (const name of candidates) {
    const filePath = path.join(dir, name)
    if (fs.existsSync(filePath)) {
      try { return fs.readFileSync(filePath, "utf-8") }
      catch { return undefined }
    }
  }
  return undefined
}

function readTools(dir: string): string[] {
  const toolsDir = path.join(dir, "tools")
  if (!fs.existsSync(toolsDir)) return []

  const tools: string[] = []
  let entries: fs.Dirent[]
  try { entries = fs.readdirSync(toolsDir, { withFileTypes: true }) }
  catch { return [] }

  for (const entry of entries) {
    if (!entry.isFile()) continue
    const toolPath = path.join(toolsDir, entry.name)
    const content = readOptional(toolsDir, [entry.name]) ?? ""
    const tool = extractToolPermission(entry.name, content)
    if (tool) tools.push(tool)
  }

  return tools
}

/**
 * Extrai a permissão MCP de um arquivo de tool.
 * Suporta formatos:
 *   - Nome do arquivo: customer_get.yaml → mcp-server-crm:customer_get (se YAML contiver mcp_server)
 *   - Conteúdo YAML com campos mcp_server e tool
 *   - Linha "permission: mcp-server-crm:customer_get"
 */
function extractToolPermission(filename: string, content: string): string | null {
  // Tentar extrair de "permission:" no conteúdo
  const permMatch = content.match(/permission:\s*['"`]?([^'"`\n]+)['"`]?/)
  if (permMatch) return permMatch[1]!.trim()

  // Tentar extrair mcp_server e tool do conteúdo
  const serverMatch = content.match(/mcp_server:\s*['"`]?([^'"`\n]+)['"`]?/)
  const toolMatch   = content.match(/\btool:\s*['"`]?([^'"`\n]+)['"`]?/)
  if (serverMatch && toolMatch) {
    return `${serverMatch[1]!.trim()}:${toolMatch[1]!.trim()}`
  }

  // Usar nome do arquivo sem extensão como nome da tool
  const toolName = path.basename(filename, path.extname(filename))
  if (toolName && toolName !== "index") {
    return toolName.includes(":") ? toolName : null
  }

  return null
}

function inferVersion(repoPath: string, manifestVersion?: string): string {
  if (manifestVersion) return manifestVersion

  // Tentar git tag
  try {
    const tag = child.execSync("git describe --tags --abbrev=0", {
      cwd:    repoPath,
      stdio:  ["ignore", "pipe", "ignore"],
      timeout: 3000,
    }).toString().trim()
    if (tag) return tag.replace(/^v/, "")
  } catch { /* sem git ou sem tags */ }

  // Tentar branch name
  try {
    const branch = child.execSync("git rev-parse --abbrev-ref HEAD", {
      cwd:    repoPath,
      stdio:  ["ignore", "pipe", "ignore"],
      timeout: 3000,
    }).toString().trim()
    if (branch && branch !== "HEAD") return `0.0.0-${branch}`
  } catch { /* sem git */ }

  return "1.0.0"
}
