/**
 * cli/import-cmd.ts
 * Comando: plughub-sdk import <repo-url-or-path>
 * Spec: PlugHub v24.0 seção 4.9.6
 */

import { GitAgentImporter } from "../gitagent/import"

export async function runImport(args: string[]): Promise<void> {
  const source = args[0]
  if (!source) {
    console.error("Uso: plughub-sdk import <url-ou-path>")
    process.exit(1)
  }

  const registryUrl = process.env["PLUGHUB_REGISTRY_URL"] ?? "http://localhost:3300"
  const tenantId    = process.env["PLUGHUB_TENANT_ID"]    ?? ""
  const apiKey      = process.env["PLUGHUB_API_KEY"]      ?? ""

  if (!tenantId) {
    console.error("PLUGHUB_TENANT_ID não configurado")
    process.exit(1)
  }

  const importer = new GitAgentImporter()
  const isLocal  = source.startsWith("/") || source.startsWith(".")

  console.log(`Importando ${isLocal ? "repositório local" : "repositório remoto"}: ${source}`)

  const result = await importer.import({
    ...(isLocal ? { localPath: source } : { repositoryUrl: source }),
    tenantId,
    registryUrl,
    apiKey,
  })

  if (result.certification_status === "passed") {
    console.log(`✅ Importação concluída: ${result.agent_type_id}`)
    console.log(`   Skills: ${result.skills_registered.join(", ")}`)
    console.log(`   Flows:  ${result.flows_registered.join(", ")}`)
  } else {
    console.error(`❌ Importação com erros:`)
    result.errors?.forEach(e => console.error(`   ${e}`))
    process.exit(1)
  }
}
