/**
 * verify/packages.ts
 * Verifica isolamento de dependências entre pacotes internos do PlugHub.
 * Spec: PlugHub v24.0 CLAUDE.md (grafo de dependências) e seção 4.6h
 *
 * Verificações:
 *   1. Nenhum pacote importa de outro interno fora das deps declaradas em CLAUDE.md
 *   2. Nenhum pacote redefine tipos de @plughub/schemas localmente
 *   3. Nenhum pacote usa 'export *'
 *   4. Nenhuma dependência circular entre pacotes internos
 */

import * as fs   from "node:fs"
import * as path from "node:path"

// ─────────────────────────────────────────────
// Grafo de dependências declarado em CLAUDE.md
// ─────────────────────────────────────────────

const INTERNAL_PACKAGES: Record<string, string> = {
  "schemas":        "@plughub/schemas",
  "sdk":            "@plughub/sdk",
  "mcp-server-plughub": "mcp-server-plughub",
  "skill-flow-engine":  "@plughub/skill-flow-engine",
  "ai-gateway":     "@plughub/ai-gateway",
  "agent-registry": "@plughub/agent-registry",
  "routing-engine": "@plughub/routing-engine",
  "rules-engine":   "@plughub/rules-engine",
}

/** Dependências permitidas por pacote (conforme CLAUDE.md) */
const ALLOWED_DEPS: Record<string, string[]> = {
  "schemas":            [],
  "sdk":                ["@plughub/schemas"],
  "mcp-server-plughub": ["@plughub/schemas"],
  "skill-flow-engine":  ["@plughub/schemas", "mcp-server-plughub"],
  "ai-gateway":         ["@plughub/schemas"],
  "agent-registry":     ["@plughub/schemas"],
  "routing-engine":     ["@plughub/schemas", "@plughub/agent-registry"],
  "rules-engine":       ["@plughub/schemas", "@plughub/routing-engine"],
}

/** Nomes de tipos canônicos do @plughub/schemas que não devem ser redefinidos */
const SCHEMA_TYPE_NAMES = new Set([
  "ContextPackage", "AgentDone", "SessionItem", "PipelineState",
  "SkillFlow", "FlowStep", "TaskStep", "ChoiceStep", "CatchStep",
  "EscalateStep", "CompleteStep", "InvokeStep", "ReasonStep", "NotifyStep",
  "PoolRegistration", "AgentTypeRegistration", "RoutingDecision",
  "SkillSchema", "SkillRegistration",
])

// ─────────────────────────────────────────────
// Tipos
// ─────────────────────────────────────────────

export interface PackageViolation {
  package:   string
  file:      string
  line?:     number
  violation: string
  severity:  "error" | "warning"
}

export interface PackageVerifyReport {
  path:        string
  status:      "passed" | "failed"
  verified_at: string
  checks:      Array<{
    name:    string
    status:  "passed" | "failed" | "warning"
    message: string
    count?:  number
  }>
  violations: PackageViolation[]
}

// ─────────────────────────────────────────────
// verifyPackages
// ─────────────────────────────────────────────

export function verifyPackages(rootPath: string): PackageVerifyReport {
  const violations: PackageViolation[] = []
  const now = new Date().toISOString()

  const packagesDir = fs.existsSync(path.join(rootPath, "packages"))
    ? path.join(rootPath, "packages")
    : rootPath

  const packageDirs = discoverPackages(packagesDir)

  // ── Check 1: Imports fora das dependências declaradas ────────────────────
  const importViolations = checkImportIsolation(packageDirs)
  violations.push(...importViolations)

  // ── Check 2: Redefinição de tipos de @plughub/schemas ────────────────────
  const redefViolations = checkSchemaRedefinition(packageDirs)
  violations.push(...redefViolations)

  // ── Check 3: Uso de 'export *' ────────────────────────────────────────────
  const exportStarViolations = checkExportStar(packageDirs)
  violations.push(...exportStarViolations)

  // ── Check 4: Dependências circulares ──────────────────────────────────────
  const circularViolations = checkCircularDeps(packageDirs)
  violations.push(...circularViolations)

  // Compilar checks
  const checks = [
    {
      name:    "imports.isolation",
      status:  importViolations.length === 0 ? "passed" : "failed",
      message: importViolations.length === 0
        ? "Nenhum import fora das dependências declaradas"
        : `${importViolations.length} violação(ões) de import`,
      count:   importViolations.length,
    },
    {
      name:    "schemas.no_redefinition",
      status:  redefViolations.length === 0 ? "passed" : "failed",
      message: redefViolations.length === 0
        ? "Nenhuma redefinição local de tipos de @plughub/schemas"
        : `${redefViolations.length} tipo(s) redefinido(s) localmente`,
      count:   redefViolations.length,
    },
    {
      name:    "exports.no_star",
      status:  exportStarViolations.length === 0 ? "passed" : "failed",
      message: exportStarViolations.length === 0
        ? "Nenhum 'export *' encontrado"
        : `${exportStarViolations.length} uso(s) de 'export *'`,
      count:   exportStarViolations.length,
    },
    {
      name:    "deps.no_circular",
      status:  circularViolations.length === 0 ? "passed" : "failed",
      message: circularViolations.length === 0
        ? "Nenhuma dependência circular entre pacotes internos"
        : `Dependência(s) circular(is) detectada(s)`,
      count:   circularViolations.length,
    },
  ] as PackageVerifyReport["checks"]

  const hasFailed = checks.some(c => c.status === "failed")

  return {
    path:        rootPath,
    status:      hasFailed ? "failed" : "passed",
    verified_at: now,
    checks,
    violations,
  }
}

// ─────────────────────────────────────────────
// Descoberta de pacotes
// ─────────────────────────────────────────────

interface PackageInfo {
  name:    string
  pkgName: string
  dir:     string
  deps:    string[]
}

function discoverPackages(packagesDir: string): PackageInfo[] {
  const result: PackageInfo[] = []
  let entries: fs.Dirent[]
  try { entries = fs.readdirSync(packagesDir, { withFileTypes: true }) }
  catch { return result }

  for (const entry of entries) {
    if (!entry.isDirectory()) continue
    const dir = path.join(packagesDir, entry.name)
    const pkgJsonPath = path.join(dir, "package.json")
    if (!fs.existsSync(pkgJsonPath)) continue

    let pkg: Record<string, unknown>
    try { pkg = JSON.parse(fs.readFileSync(pkgJsonPath, "utf-8")) }
    catch { continue }

    const pkgName = typeof pkg["name"] === "string" ? pkg["name"] : entry.name
    const allDeps = {
      ...(pkg["dependencies"] as Record<string, string> ?? {}),
      ...(pkg["devDependencies"] as Record<string, string> ?? {}),
    }
    const deps = Object.keys(allDeps)

    result.push({ name: entry.name, pkgName, dir, deps })
  }

  return result
}

// ─────────────────────────────────────────────
// Check 1: Import isolation
// ─────────────────────────────────────────────

function checkImportIsolation(packages: PackageInfo[]): PackageViolation[] {
  const violations: PackageViolation[] = []
  const internalPkgNames = new Set(Object.values(INTERNAL_PACKAGES))

  for (const pkg of packages) {
    const allowed = ALLOWED_DEPS[pkg.name] ?? pkg.deps.filter(d => internalPkgNames.has(d))
    const sourceFiles = findSourceFiles(pkg.dir)

    for (const file of sourceFiles) {
      const lines = readLines(file)
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i] ?? ""
        const importMatch = line.match(/(?:import|require|from)\s+['"`](@plughub\/[^'"` ]+|mcp-server-[^'"` ]+)['"`]/)
        if (!importMatch) continue

        const imported = importMatch[1]!
        if (!internalPkgNames.has(imported)) continue
        if (allowed.includes(imported)) continue

        violations.push({
          package:   pkg.pkgName,
          file:      path.relative(pkg.dir, file).replace(/\\/g, "/"),
          line:      i + 1,
          violation: `import de '${imported}' não declarado nas dependências do pacote`,
          severity:  "error",
        })
      }
    }
  }

  return violations
}

// ─────────────────────────────────────────────
// Check 2: Schema redefinition
// ─────────────────────────────────────────────

function checkSchemaRedefinition(packages: PackageInfo[]): PackageViolation[] {
  const violations: PackageViolation[] = []

  for (const pkg of packages) {
    if (pkg.name === "schemas") continue  // schemas pode definir tudo
    const sourceFiles = findSourceFiles(pkg.dir)

    for (const file of sourceFiles) {
      const lines = readLines(file)
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i] ?? ""

        // Detectar definições locais de tipos de schema:
        // export type X = ... | export interface X | const XSchema = z.object(
        for (const typeName of SCHEMA_TYPE_NAMES) {
          const defPattern = new RegExp(
            `(?:export\\s+(?:type|interface|const)\\s+${typeName}|` +
            `${typeName}Schema\\s*=\\s*z\\.(?:object|string|array|enum))`
          )
          if (defPattern.test(line) && !line.includes("// plughub-ok")) {
            violations.push({
              package:   pkg.pkgName,
              file:      path.relative(pkg.dir, file).replace(/\\/g, "/"),
              line:      i + 1,
              violation: `redefinição local de tipo '${typeName}' — usar import de @plughub/schemas`,
              severity:  "error",
            })
          }
        }
      }
    }
  }

  return violations
}

// ─────────────────────────────────────────────
// Check 3: export *
// ─────────────────────────────────────────────

function checkExportStar(packages: PackageInfo[]): PackageViolation[] {
  const violations: PackageViolation[] = []

  for (const pkg of packages) {
    const sourceFiles = findSourceFiles(pkg.dir)
    for (const file of sourceFiles) {
      const lines = readLines(file)
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i] ?? ""
        if (/^\s*export\s+\*\s*(from\s+|;|$)/.test(line) && !line.includes("// plughub-ok")) {
          violations.push({
            package:   pkg.pkgName,
            file:      path.relative(pkg.dir, file).replace(/\\/g, "/"),
            line:      i + 1,
            violation: "uso de 'export *' — usar exports nomeados explícitos (CLAUDE.md invariante)",
            severity:  "error",
          })
        }
      }
    }
  }

  return violations
}

// ─────────────────────────────────────────────
// Check 4: Circular dependencies
// ─────────────────────────────────────────────

function checkCircularDeps(packages: PackageInfo[]): PackageViolation[] {
  const violations: PackageViolation[] = []
  const internalPkgNames = new Set(Object.values(INTERNAL_PACKAGES))

  // Construir grafo de dependências inter-pacotes
  const graph = new Map<string, Set<string>>()
  const nameMap = new Map<string, string>()  // pkgName → dirName

  for (const pkg of packages) {
    graph.set(pkg.pkgName, new Set())
    nameMap.set(pkg.pkgName, pkg.name)
  }

  for (const pkg of packages) {
    for (const dep of pkg.deps) {
      if (internalPkgNames.has(dep)) {
        graph.get(pkg.pkgName)?.add(dep)
      }
    }
  }

  // DFS para detectar ciclos
  const visited  = new Set<string>()
  const visiting = new Set<string>()

  function dfs(node: string, pathStack: string[]): boolean {
    if (visiting.has(node)) {
      const cycleStart = pathStack.indexOf(node)
      const cycle = pathStack.slice(cycleStart).concat(node)
      violations.push({
        package:   node,
        file:      "package.json",
        violation: `dependência circular: ${cycle.join(" → ")}`,
        severity:  "error",
      })
      return true
    }
    if (visited.has(node)) return false

    visiting.add(node)
    for (const dep of (graph.get(node) ?? [])) {
      dfs(dep, [...pathStack, node])
    }
    visiting.delete(node)
    visited.add(node)
    return false
  }

  for (const pkg of packages) {
    dfs(pkg.pkgName, [])
  }

  return violations
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

const SOURCE_EXTENSIONS = [".ts", ".js", ".mjs"]
const SKIP_DIRS = new Set(["node_modules", "dist", ".git", "__pycache__"])

function findSourceFiles(dirPath: string): string[] {
  const files: string[] = []
  function scan(dir: string): void {
    let entries: fs.Dirent[]
    try { entries = fs.readdirSync(dir, { withFileTypes: true }) }
    catch { return }
    for (const e of entries) {
      if (e.name.startsWith(".") || SKIP_DIRS.has(e.name)) continue
      const full = path.join(dir, e.name)
      if (e.isDirectory()) scan(full)
      else if (e.isFile() && SOURCE_EXTENSIONS.some(ext => e.name.endsWith(ext))) files.push(full)
    }
  }
  scan(dirPath)
  return files
}

function readLines(file: string): string[] {
  try { return fs.readFileSync(file, "utf-8").split("\n") }
  catch { return [] }
}
