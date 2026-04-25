/**
 * certify/lifecycle.ts
 * Análise estática do agente:
 *  - eventos do ciclo de vida implementados
 *  - issue_status em todos os agent_done
 *  - handoff_reason quando outcome !== 'resolved'
 *  - tools MCP chamadas vs. permissions declaradas
 *  - chamadas diretas a backends
 * Spec: PlugHub v24.0 seção 4.2 e 4.6e
 */

import * as fs   from "node:fs"
import * as path from "node:path"

// ─────────────────────────────────────────────
// Tipos
// ─────────────────────────────────────────────

export interface FileLocation {
  file: string
  line: number
}

export interface LifecycleResult {
  /** Eventos encontrados */
  found: Record<string, FileLocation>
  /** Eventos obrigatórios ausentes */
  missing: string[]
}

export interface AgentDoneResult {
  /** Ocorrências de agent_done sem issue_status */
  missingIssueStatus: FileLocation[]
  /** Ocorrências de agent_done com outcome não-resolved sem handoff_reason */
  missingHandoffReason: FileLocation[]
}

export interface PermissionsResult {
  /** Chamadas a tools MCP não declaradas em permissions */
  undeclaredTools: Array<FileLocation & { tool: string }>
  /** Chamadas diretas a backends (HTTP, DB, infra) */
  directBackendCalls: Array<FileLocation & { detail: string }>
}

// ─────────────────────────────────────────────
// Eventos obrigatórios do ciclo de vida
// ─────────────────────────────────────────────

const REQUIRED_LIFECYCLE_EVENTS = [
  "agent_login",
  "agent_ready",
  "agent_busy",
  "agent_done",
  "agent_pause",
  "agent_logout",
] as const

// ─────────────────────────────────────────────
// checkLifecycleEvents
// ─────────────────────────────────────────────

export function checkLifecycleEvents(dirPath: string): LifecycleResult {
  const sourceFiles = findSourceFiles(dirPath)
  const found: Record<string, FileLocation> = {}

  for (const file of sourceFiles) {
    const lines = readLines(file)
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i] ?? ""
      for (const event of REQUIRED_LIFECYCLE_EVENTS) {
        if (!(event in found) && line.includes(event)) {
          found[event] = { file: relative(dirPath, file), line: i + 1 }
        }
      }
    }
  }

  const missing = REQUIRED_LIFECYCLE_EVENTS.filter(e => !(e in found))
  return { found, missing }
}

// ─────────────────────────────────────────────
// checkAgentDoneContract
// ─────────────────────────────────────────────

/** Contexto (linhas) a analisar ao redor de cada agent_done */
const DONE_CTX_LINES = 12

export function checkAgentDoneContract(dirPath: string): AgentDoneResult {
  const sourceFiles = findSourceFiles(dirPath)
  const missingIssueStatus:   FileLocation[] = []
  const missingHandoffReason: FileLocation[] = []

  for (const file of sourceFiles) {
    const lines = readLines(file)
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i] ?? ""
      if (!line.includes("agent_done")) continue

      const loc: FileLocation = { file: relative(dirPath, file), line: i + 1 }
      const ctx = lines.slice(i, i + DONE_CTX_LINES).join("\n")

      // issue_status deve estar presente no contexto do agent_done
      if (!ctx.includes("issue_status")) {
        missingIssueStatus.push(loc)
      }

      // handoff_reason quando outcome não é 'resolved'
      const hasNonResolved = /outcome['":\s]+(escalated|transferred|callback|handoff|error|failed)/
        .test(ctx)
      if (hasNonResolved && !ctx.includes("handoff_reason")) {
        missingHandoffReason.push(loc)
      }
    }
  }

  return { missingIssueStatus, missingHandoffReason }
}

// ─────────────────────────────────────────────
// checkPermissions
// ─────────────────────────────────────────────

/** Padrões de chamadas a tools MCP (TypeScript/Python) */
const MCP_CALL_PATTERNS = [
  /(?:mcp|client|server)\.(?:callTool|call_tool|call|invoke|request)\s*\(\s*['"`]([^'"` ,]+)['"`]/g,
  /tool_name\s*=\s*['"`]([^'"` ]+)['"`]/g,
  /await\s+(?:mcp|client)\.(?:callTool|call)\s*\(\s*\{[^}]*name\s*:\s*['"`]([^'"` ]+)['"`]/g,
]

/** Padrões de acesso direto a infraestrutura de backend */
const BACKEND_PATTERNS: Array<{ pattern: RegExp; detail: string }> = [
  { pattern: /\baxios\.\s*(?:get|post|put|delete|patch)\s*\(/, detail: "chamada HTTP direta via axios" },
  { pattern: /\bfetch\s*\(\s*(?:['"`])https?:\/\//, detail: "fetch HTTP direto" },
  { pattern: /\bnew\s+(?:WebSocket|XMLHttpRequest)\s*\(/, detail: "WebSocket/XHR direto" },
  { pattern: /(?:require|import).*(?:ioredis|redis\.createClient|kafkajs|kafka-node|pg\.Pool|mysql\.createPool|mongoose)/, detail: "acesso direto a Redis/Kafka/DB" },
  { pattern: /(?:pg|mysql|mongoose|mongodb)\s*\.\s*(?:connect|createConnection|query)/, detail: "conexão direta a banco de dados" },
]

export function checkPermissions(
  dirPath:             string,
  declaredPermissions: string[],
): PermissionsResult {
  const sourceFiles  = findSourceFiles(dirPath)
  const undeclaredTools:    Array<FileLocation & { tool: string }>   = []
  const directBackendCalls: Array<FileLocation & { detail: string }> = []

  for (const file of sourceFiles) {
    const content  = fs.readFileSync(file, "utf-8")
    const lines    = content.split("\n")
    const relFile  = relative(dirPath, file)

    // Checar chamadas a tools MCP
    for (const pattern of MCP_CALL_PATTERNS) {
      pattern.lastIndex = 0
      let m: RegExpExecArray | null
      while ((m = pattern.exec(content)) !== null) {
        const toolName = m[1] ?? m[2] ?? m[3]
        if (!toolName) continue
        const declared = declaredPermissions.some(
          p => p.endsWith(`:${toolName}`) || p === toolName || p.includes(toolName),
        )
        if (!declared) {
          const lineNum = content.slice(0, m.index).split("\n").length
          undeclaredTools.push({ tool: toolName, file: relFile, line: lineNum })
        }
      }
    }

    // Checar chamadas diretas a backend
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i] ?? ""
      if (line.trimStart().startsWith("//") || line.trimStart().startsWith("#")) continue
      if (line.includes("// plughub-ok") || line.includes("# plughub-ok")) continue

      for (const { pattern, detail } of BACKEND_PATTERNS) {
        if (pattern.test(line)) {
          directBackendCalls.push({ file: relFile, line: i + 1, detail })
          break
        }
      }
    }
  }

  return { undeclaredTools, directBackendCalls }
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

const SOURCE_EXTENSIONS = [".ts", ".py", ".js", ".mjs", ".tsx"]
const SKIP_DIRS = new Set(["node_modules", "__pycache__", "dist", ".git", ".venv", "venv"])

function findSourceFiles(dirPath: string): string[] {
  const files: string[] = []

  function scan(dir: string): void {
    let entries: fs.Dirent[]
    try { entries = fs.readdirSync(dir, { withFileTypes: true }) }
    catch  { return }

    for (const entry of entries) {
      if (entry.name.startsWith(".")) continue
      if (SKIP_DIRS.has(entry.name))  continue
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        scan(full)
      } else if (entry.isFile() && SOURCE_EXTENSIONS.some(ext => entry.name.endsWith(ext))) {
        // Skip test files
        if (entry.name.match(/\.(test|spec)\.(ts|js)$/) || entry.name.includes("_test.")) continue
        files.push(full)
      }
    }
  }

  scan(dirPath)
  return files
}

function readLines(file: string): string[] {
  try { return fs.readFileSync(file, "utf-8").split("\n") }
  catch { return [] }
}

function relative(base: string, file: string): string {
  return path.relative(base, file).replace(/\\/g, "/")
}
