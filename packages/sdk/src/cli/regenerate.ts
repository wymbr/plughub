/**
 * cli/regenerate.ts
 * plughub-sdk regenerate [gitagent-repo-path] — converte GitAgent para nativo PlugHub.
 * Spec: PlugHub v24.0 seção 4.6i
 *
 * Uso:
 *   plughub-sdk regenerate ./copilot-agent/
 *   plughub-sdk regenerate ./copilot-agent/ --output ./output/
 *   plughub-sdk regenerate ./copilot-agent/ --json
 */

import { Command }         from "commander"
import * as path           from "path"
import { readGitAgent }    from "../regenerate/reader"
import { convertGitAgent } from "../regenerate/convert"
import type { ConvertResult } from "../regenerate/convert"

export function registerRegenerateCommand(program: Command): void {
  program
    .command("regenerate [dir]")
    .description("Converte repositório GitAgent para formato nativo PlugHub (spec 4.6i)")
    .option("--output <dir>", "Diretório de saída (padrão: <dir>/output)")
    .option("--json",         "Saída em formato JSON (para CI/CD pipelines)")
    .action((dir: string | undefined, opts: { output?: string; json?: boolean }) => {
      const repoPath  = path.resolve(dir ?? ".")
      const outputDir = opts.output
        ? path.resolve(opts.output)
        : path.join(repoPath, "output")

      // Leitura dos artefatos GitAgent
      let artifacts: ReturnType<typeof readGitAgent>
      try {
        artifacts = readGitAgent(repoPath)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        if (opts.json) {
          console.log(JSON.stringify({ status: "error", error: msg }, null, 2))
        } else {
          console.error(`\n❌ Erro ao ler artefatos GitAgent: ${msg}`)
        }
        process.exit(1)
      }

      // Conversão para formato nativo
      let result: ConvertResult
      try {
        result = convertGitAgent(artifacts, outputDir)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        if (opts.json) {
          console.log(JSON.stringify({ status: "error", error: msg }, null, 2))
        } else {
          console.error(`\n❌ Regeneração abortada:\n${msg}`)
        }
        process.exit(1)
      }

      const summary = {
        status:      "success",
        output_path: result.outputPath,
        files:       result.files,
        warnings:    result.warnings,
      }

      if (opts.json) {
        console.log(JSON.stringify(summary, null, 2))
      } else {
        console.log(`\n✅ Regeneração concluída — ${result.files.length} arquivo(s) gerado(s)`)
        console.log(`   Saída: ${result.outputPath}\n`)
        for (const f of result.files) {
          console.log(`   ✓ ${f}`)
        }
        if (result.warnings.length > 0) {
          console.log(`\nAvisos:`)
          for (const w of result.warnings) {
            console.log(`  ⚠  ${w}`)
          }
        }
        console.log()
      }

      process.exit(0)
    })
}
