/**
 * cli/skill-extract.ts
 * plughub-sdk skill-extract [agent-path] — extrai skill de agente existente.
 * Spec: PlugHub v24.0 seção 4.6j
 *
 * Uso:
 *   plughub-sdk skill-extract ./meu-agente/
 *   plughub-sdk skill-extract ./meu-agente/ --output skill-draft.json
 *   plughub-sdk skill-extract ./meu-agente/ --json
 */

import { Command }      from "commander"
import * as path        from "path"
import * as fs          from "fs"
import { readGitAgent } from "../regenerate/reader"

export function registerSkillExtractCommand(program: Command): void {
  program
    .command("skill-extract [dir]")
    .description("Extrai rascunho de skill registrável de agente GitAgent (spec 4.6j)")
    .option("--output <file>", "Salvar rascunho de skill em arquivo JSON")
    .option("--json",          "Saída em formato JSON (para CI/CD pipelines)")
    .action((dir: string | undefined, opts: { output?: string; json?: boolean }) => {
      const agentPath = path.resolve(dir ?? ".")

      let artifacts: ReturnType<typeof readGitAgent>
      try {
        artifacts = readGitAgent(agentPath)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        if (opts.json) {
          console.log(JSON.stringify({ status: "error", error: msg }, null, 2))
        } else {
          console.error(`\n❌ Erro ao ler agente: ${msg}`)
        }
        process.exit(1)
      }

      const manifest   = artifacts.manifest
      const agentTypeId = manifest?.agent_type_id ?? path.basename(agentPath)

      // Derivar skill_id do agent_type_id (agente_X_vN → skill_X_vN)
      const skillId = `skill_${agentTypeId.replace(/^agente_/, "")}`

      // Extrair tools das permissions MCP
      const tools = (manifest?.permissions ?? []).map(p => {
        const colonIdx = p.indexOf(":")
        if (colonIdx !== -1) {
          return { mcp_server: p.slice(0, colonIdx), tool: p.slice(colonIdx + 1) }
        }
        return { tool: p }
      })

      // Extrair version_policy do SKILL.md se existir
      let versionPolicy = "stable"
      if (artifacts.skill) {
        const policyMatch = artifacts.skill.match(/version_policy[:\s]+['\"`]?(stable|latest|exact)['\"`]?/)
        if (policyMatch?.[1]) versionPolicy = policyMatch[1]
      }

      const skillDraft = {
        skill_id:        skillId,
        version:         artifacts.version,
        version_policy:  versionPolicy,
        agent_type_id:   agentTypeId,
        framework:       manifest?.framework,
        execution_model: manifest?.execution_model,
        pools:           manifest?.pools ?? [],
        tools,
        _draft:         true,
        _source:        agentPath,
        _generated_at:  new Date().toISOString(),
        _notes: [
          "Revisar skill_id, version e pools antes de registrar no Skill Registry",
          "Completar campos instruction e evaluation — requerem revisão manual",
        ],
      }

      const outputJson = JSON.stringify(skillDraft, null, 2)

      if (opts.output) {
        const outputPath = path.resolve(opts.output)
        fs.writeFileSync(outputPath, outputJson)
        if (!opts.json) {
          console.log(`\n✅ Rascunho de skill salvo em: ${outputPath}`)
          console.log("   Revise antes de registrar no Skill Registry.\n")
        }
      }

      if (opts.json) {
        console.log(outputJson)
      } else if (!opts.output) {
        console.log(`\n📋 Rascunho de skill (${skillId}):\n`)
        console.log(outputJson)
        console.log()
      }

      process.exit(0)
    })
}
