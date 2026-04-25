/**
 * cli/certify.ts
 * plughub-sdk certify [path] — certifica repositório GitAgent ou diretório de agente.
 * Spec: PlugHub v24.0 seção 4.6e
 *
 * Uso:
 *   plughub-sdk certify
 *   plughub-sdk certify ./meu-agente/
 *   plughub-sdk certify ./meu-agente/ --json
 */

import { Command }    from "commander"
import * as path      from "path"
import { certifyDir } from "../certify/dir"
import type { DirCertifyReport, CertifyCheckItem } from "../certify/dir"

export function registerCertifyCommand(program: Command): void {
  program
    .command("certify [dir]")
    .description("Certifica repositório GitAgent ou diretório de agente (spec 4.6e)")
    .option("--json", "Saída em formato JSON (para CI/CD pipelines)")
    .action((dir: string | undefined, opts: { json?: boolean }) => {
      const dirPath = path.resolve(dir ?? ".")

      let report: DirCertifyReport
      try {
        report = certifyDir(dirPath)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        if (opts.json) {
          console.log(JSON.stringify({ status: "error", error: msg }, null, 2))
        } else {
          console.error(`\n❌ Erro ao certificar: ${msg}`)
        }
        process.exit(1)
      }

      if (opts.json) {
        console.log(JSON.stringify(report, null, 2))
      } else {
        _printReport(report)
      }

      process.exit(report.status === "certified" ? 0 : 1)
    })
}

function _printReport(report: DirCertifyReport): void {
  const icon = report.status === "certified" ? "✅" : "❌"
  console.log(`\n${icon} ${report.status.toUpperCase()} — ${report.agent_type_id}`)
  if (report.version) console.log(`   Versão: ${report.version}`)
  console.log(`   Path:   ${report.path}`)
  console.log(`   Em:     ${report.certified_at}\n`)
  console.log("Checks:")

  for (const check of report.checks) {
    _printCheck(check)
  }
  console.log()
}

function _printCheck(check: CertifyCheckItem): void {
  const checkIcon = check.status === "passed" ? "  ✓" : check.status === "warning" ? "  ⚠" : "  ✗"
  console.log(`${checkIcon} ${check.name}: ${check.message}`)
  if (check.detail) {
    console.log(`      ${check.detail}`)
  }
  if (check.errors && check.errors.length > 0) {
    const shown = check.errors.slice(0, 5)
    for (const err of shown) {
      const loc = err.file ? ` (${err.file}${err.line != null ? `:${err.line}` : ""})` : ""
      console.log(`      ↳ ${err.message}${loc}`)
    }
    if (check.errors.length > 5) {
      console.log(`      ↳ ... e mais ${check.errors.length - 5} erro(s)`)
    }
  }
}
