/**
 * cli/validate-adapter.ts
 * plughub-sdk validate-adapter — valida o PlugHubAdapter sem precisar do handler.
 * Spec: PlugHub v24.0 seção 4.6d
 *
 * Uso:
 *   plughub-sdk validate-adapter --adapter ./meu-agente.ts
 *   plughub-sdk validate-adapter --adapter ./meu-agente.ts --sample ./sample-context.json
 *
 * Útil para validar o mapeamento de contexto isoladamente,
 * sem precisar de um ambiente completo ou de um handler funcional.
 */

import { Command }           from "commander"
import * as path             from "path"
import * as fs               from "fs"
import { ContextPackageSchema } from "@plughub/schemas"
import type { PlugHubAdapter }  from "../adapter"

export function registerValidateCommand(program: Command): void {
  program
    .command("validate-adapter")
    .description("Valida o PlugHubAdapter e seus mapeamentos (spec 4.6d)")
    .requiredOption("--adapter <path>", "Arquivo que exporta o adapter")
    .option("--sample <path>",          "context_package de exemplo em JSON para simular fromPlatform()")
    .action(async (opts: {
      adapter: string
      sample?: string
    }) => {
      const adapterPath = path.resolve(opts.adapter)

      let adapterModule: Record<string, unknown>
      try {
        adapterModule = await import(adapterPath) as Record<string, unknown>
      } catch (e) {
        console.error(`\n❌ Erro ao carregar arquivo: ${adapterPath}`)
        console.error(e instanceof Error ? e.message : String(e))
        process.exit(1)
      }

      if (!adapterModule["adapter"]) {
        console.error("\n❌ O arquivo deve exportar: export const adapter = new PlugHubAdapter({ ... })")
        process.exit(1)
      }

      const adapter = adapterModule["adapter"] as PlugHubAdapter
      console.log("\n🔍 Validando PlugHubAdapter ...\n")

      // ── Check 1: Campos obrigatórios mapeados ──
      const resultFields = Object.keys(adapter.config.result_map)
      const requiredFields = ["outcome", "issue_status"]
      const missing = requiredFields.filter(f => !resultFields.includes(f))

      if (missing.length > 0) {
        console.log(`❌ result_map sem campos obrigatórios: ${missing.join(", ")}`)
        process.exit(1)
      }
      console.log("✓ result_map — campos obrigatórios presentes (outcome, issue_status)")

      // ── Check 2: context_map declarado ──
      const contextFields = Object.keys(adapter.config.context_map)
      if (contextFields.length === 0) {
        console.log("⚠  context_map vazio — agente não receberá contexto mapeado da plataforma")
      } else {
        console.log(`✓ context_map — ${contextFields.length} campo(s) mapeado(s):`)
        for (const [platform, agent] of Object.entries(adapter.config.context_map)) {
          console.log(`    ${platform} → ${agent}`)
        }
      }

      // ── Check 3: outcome_map declarado ──
      if (!adapter.config.outcome_map) {
        console.log("⚠  outcome_map ausente — valores de outcome do agente devem coincidir exatamente com os da plataforma")
        console.log("    Valores válidos: resolved | escalated_human | transferred_agent | callback")
      } else {
        const outcomeEntries = Object.entries(adapter.config.outcome_map)
        console.log(`✓ outcome_map — ${outcomeEntries.length} mapeamento(s):`)
        for (const [agent, platform] of outcomeEntries) {
          console.log(`    "${agent}" → "${platform}"`)
        }
      }

      // ── Check 4: Simulação com sample se fornecido ──
      if (opts.sample) {
        const samplePath = path.resolve(opts.sample)
        let rawSample: unknown
        try {
          rawSample = JSON.parse(fs.readFileSync(samplePath, "utf-8"))
        } catch (e) {
          console.error(`\n❌ Erro ao ler sample: ${samplePath}`)
          process.exit(1)
        }

        try {
          const pkg = ContextPackageSchema.parse(rawSample)
          const mapped = adapter.fromPlatform(pkg)
          console.log("\n✓ Simulação fromPlatform() — contexto mapeado:")
          console.log(JSON.stringify(mapped, null, 2))
        } catch (e) {
          console.error("\n❌ Erro ao simular fromPlatform():")
          console.error(e instanceof Error ? e.message : String(e))
          process.exit(1)
        }
      }

      console.log("\n✅ Adapter válido")
      process.exit(0)
    })
}
