/**
 * portability.ts
 * Verificação de portabilidade de agentes nativos.
 * Spec: PlugHub v24.0 seção 4.6h
 *
 * Verifica se um agente nativo tem dependências implícitas que
 * comprometeriam sua portabilidade fora da plataforma.
 *
 * Complementar à certificação:
 *   certify          → verifica se o agente funciona NA plataforma
 *   verify-portability → verifica se o agente funciona FORA dela
 */

export interface PortabilityCheck {
  name:    string
  status:  "passed" | "failed" | "warning"
  message: string
  detail?: string
}

export interface PortabilityReport {
  agent_type_id: string
  status:        "portable" | "not_portable" | "warnings"
  checks:        PortabilityCheck[]
  verified_at:   string
}

export interface PortabilityVerifyConfig {
  agent_type_id: string
  /** Código fonte do agente como string — analisado estaticamente */
  source_code:   string
  /** Arquivos de dependências (package.json, requirements.txt) */
  dependencies?: string
}

export async function verifyPortability(
  config: PortabilityVerifyConfig
): Promise<PortabilityReport> {
  const checks: PortabilityCheck[] = []
  const src = config.source_code

  // ── Check 1: Sem import direto de schemas internos ──
  const internalImports = [
    /@plughub\/schemas/,
    /context_package/,
    /agent_done/,
    /pipeline_state/,
  ]
  const hasDirectInternalImport = internalImports.some(pattern =>
    pattern.test(src) && !src.includes("// plughub-ignore-portability")
  )
  checks.push({
    name:    "no_direct_platform_schema_import",
    status:  hasDirectInternalImport ? "failed" : "passed",
    message: hasDirectInternalImport
      ? "Import direto de schema interno detectado — use PlugHubAdapter como interface"
      : "Sem imports diretos de schemas internos da plataforma",
    detail: hasDirectInternalImport
      ? "O agente deve receber contexto apenas via adapter.fromPlatform(), nunca acessar ContextPackage diretamente"
      : undefined,
  })

  // ── Check 2: Sem referência a URLs internas da plataforma ──
  const internalUrlPattern = /plughub\.internal|mcp-server-plughub\.internal|\.plughub\.svc/
  const hasInternalUrl = internalUrlPattern.test(src)
  checks.push({
    name:    "no_internal_url_hardcoded",
    status:  hasInternalUrl ? "failed" : "passed",
    message: hasInternalUrl
      ? "URL interna da plataforma hardcoded detectada — use variável de ambiente"
      : "Sem URLs internas hardcoded",
  })

  // ── Check 3: PlugHubAdapter declarado ──
  const hasAdapter = /PlugHubAdapter|definePlugHubAgent/.test(src)
  checks.push({
    name:    "adapter_declared",
    status:  hasAdapter ? "passed" : "warning",
    message: hasAdapter
      ? "PlugHubAdapter declarado — interface de portabilidade presente"
      : "PlugHubAdapter não detectado — agente pode ter dependências implícitas não verificáveis",
  })

  // ── Check 4: Sem acesso direto ao Redis ou Kafka ──
  const infraPattern = /ioredis|kafkajs|redis\.createClient|new Kafka/
  const hasDirectInfra = infraPattern.test(src)
  checks.push({
    name:    "no_direct_infra_access",
    status:  hasDirectInfra ? "failed" : "passed",
    message: hasDirectInfra
      ? "Acesso direto a Redis ou Kafka detectado — use tools MCP via mcp-server-plughub"
      : "Sem acesso direto a infraestrutura interna",
    detail: hasDirectInfra
      ? "Agentes portáveis acessam dados apenas via MCP Servers autorizados (spec 4.2)"
      : undefined,
  })

  // ── Check 5: Dependências externas verificáveis ──
  if (config.dependencies) {
    const hasLockingDeps = /plughub-runtime|@plughub\/core/.test(config.dependencies)
    checks.push({
      name:    "no_runtime_lock_dependencies",
      status:  hasLockingDeps ? "failed" : "passed",
      message: hasLockingDeps
        ? "Dependência de runtime proprietário da plataforma detectada"
        : "Dependências sem lock de runtime proprietário",
    })
  }

  // Resultado final
  const hasFailed  = checks.some(c => c.status === "failed")
  const hasWarning = checks.some(c => c.status === "warning")

  const status: PortabilityReport["status"] = hasFailed
    ? "not_portable"
    : hasWarning
      ? "warnings"
      : "portable"

  return {
    agent_type_id: config.agent_type_id,
    status,
    checks,
    verified_at:   new Date().toISOString(),
  }
}
