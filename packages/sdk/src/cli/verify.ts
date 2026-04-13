/**
 * cli/verify.ts
 * plughub-sdk verify-portability [path] — verifica isolamento de dependências.
 * Spec: PlugHub v24.0 seção 4.6h
 *
 * Uso:
 *   plughub-sdk verify-portability
 *   plughub-sdk verify-portability ./plughub/
 *   plughub-sdk verify-portability ./plughub/ --json
 */

import { Command }         from "commander"
import * as path           from "path"
import { verifyPackages }  from "../verify/packages"
import type { PackageVerifyReport } from "../verify/packages"

export function registerVerifyCommand(program: Command): void {
  program
    .command("verify-portability [dir]")
    .description("Verifica isolamento de dependências entre pacotes internos (spec 4.6h)")
    .option("--json", "Saída em formato JSON (para CI/CD pipelines)")
    .action((dir: string | undefined, opts: { json?: boolean }) => {
      const dirPath = path.resolve(dir ?? ".")

      let report: PackageVerifyReport
      try {
        report = verifyPackages(dirPath)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        if (opts.json) {
          console.log(JSON.stringify({ status: "error", error: msg }, null, 2))
        } else {
          console.error(`\n❌ Erro ao verificar: ${msg}`)
        }
        process.exit(1)
      }

      if (opts.json) {
        console.log(JSON.stringify(report, null, 2))
      } else {
        _printReport(report)
      }

      process.exit(report.status === "passed" ? 0 : 1)
    })
}

function _printReport(report: PackageVerifyReport): void {
  const icon = report.status === "passed" ? "✅" : "❌"
  console.log(`\n${icon} ${report.status.toUpperCase()} — ${report.path}`)
  console.log(`   Em: ${report.verified_at}\n`)
  console.log("Checks:")

  for (const check of report.checks) {
    const checkIcon = check.status === "passed" ? "  ✓" : check.status === "warning" ? "  ⚠" : "  ✗"
    console.log(`${checkIcon} ${check.name}: ${check.message}`)
  }

  if (report.violations.length > 0) {
    console.log(`\nViolações (${report.violations.length}):`)
    const shown = report.violations.slice(0, 20)
    for (const v of shown) {
      const loc = v.line != null ? `:${v.line}` : ""
      console.log(`  [${v.severity}] ${v.package} — ${v.file}${loc}`)
      console.log(`         ${v.violation}`)
    }
    if (report.violations.length > 20) {
      console.log(`  ... e mais ${report.violations.length - 20} violação(ões)`)
    }
  }
  console.log()
}
