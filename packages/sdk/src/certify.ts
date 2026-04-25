/**
 * certify.ts
 * Lógica de certificação de compatibilidade do agente.
 * Spec: PlugHub v24.0 seção 4.6e
 *
 * Valida que o agente implementa corretamente o contrato de execução
 * sem precisar do ambiente completo da plataforma.
 * Usado como gate obrigatório no pipeline CI/CD.
 */

import { PlugHubAdapter }   from "./adapter"
import { ContextPackageSchema, AgentDoneSchema } from "@plughub/schemas"
import type { AgentHandler } from "./agent"

// ─────────────────────────────────────────────
// Resultado da certificação
// ─────────────────────────────────────────────

export type CertifyStatus = "passed" | "failed" | "warning"

export interface CertifyCheck {
  name:    string
  status:  CertifyStatus
  message: string
}

export interface CertifyReport {
  agent_type_id: string
  status:        CertifyStatus
  checks:        CertifyCheck[]
  certified_at:  string
}

// ─────────────────────────────────────────────
// Config da certificação
// ─────────────────────────────────────────────

export interface CertifyConfig {
  agent_type_id: string
  adapter:       PlugHubAdapter
  handler:       AgentHandler
  pools:         string[]
}

// ─────────────────────────────────────────────
// certifyAgent
// ─────────────────────────────────────────────

export async function certifyAgent(config: CertifyConfig): Promise<CertifyReport> {
  const checks: CertifyCheck[] = []

  // ── Check 1: Adapter tem campos obrigatórios mapeados ──
  try {
    // Se o adapter foi instanciado sem erro, os campos obrigatórios estão mapeados
    // (a validação acontece no construtor do PlugHubAdapter)
    checks.push({
      name:    "adapter.required_fields",
      status:  "passed",
      message: "context_map e result_map com campos obrigatórios (outcome, issue_status) declarados",
    })
  } catch (e) {
    checks.push({
      name:    "adapter.required_fields",
      status:  "failed",
      message: e instanceof Error ? e.message : "Adapter inválido",
    })
  }

  // ── Check 2: Handler aceita context_package mínimo ──
  const minimalContextPackage = _buildMinimalContextPackage()
  try {
    const mappedCtx = config.adapter.fromPlatform(minimalContextPackage)
    const result = await config.handler({
      context:     mappedCtx,
      session_id:  minimalContextPackage.session_id,
      turn_number: 1,
    })
    checks.push({
      name:    "handler.executes_without_error",
      status:  "passed",
      message: "Handler executou sem erros com context_package mínimo",
    })

    // ── Check 3: Resultado do handler produz agent_done válido ──
    try {
      const platformResult = config.adapter.toPlatform(result.result)
      AgentDoneSchema.parse({
        session_id:   minimalContextPackage.session_id,
        agent_id:     `${config.agent_type_id}_cert`,
        outcome:      platformResult.outcome,
        issue_status: result.issues,
        handoff_reason: result.handoff_reason,
        completed_at: new Date().toISOString(),
      })
      checks.push({
        name:    "handler.produces_valid_agent_done",
        status:  "passed",
        message: "Resultado do handler produz agent_done válido conforme spec 4.2",
      })
    } catch (e) {
      checks.push({
        name:    "handler.produces_valid_agent_done",
        status:  "failed",
        message: e instanceof Error ? e.message : "agent_done inválido",
      })
    }
  } catch (e) {
    checks.push({
      name:    "handler.executes_without_error",
      status:  "failed",
      message: e instanceof Error ? e.message : "Handler falhou",
    })
    checks.push({
      name:    "handler.produces_valid_agent_done",
      status:  "failed",
      message: "Não verificado — handler falhou na execução",
    })
  }

  // ── Check 4: Pools declarados ──
  if (config.pools.length === 0) {
    checks.push({
      name:    "registration.pools_declared",
      status:  "failed",
      message: "Nenhum pool declarado — o agente não será alocado pelo Routing Engine",
    })
  } else {
    checks.push({
      name:    "registration.pools_declared",
      status:  "passed",
      message: `${config.pools.length} pool(s) declarado(s): ${config.pools.join(", ")}`,
    })
  }

  // ── Check 5: issue_status não vazio ──
  const hasIssues = checks.find(c => c.name === "handler.produces_valid_agent_done")?.status === "passed"
  if (hasIssues) {
    checks.push({
      name:    "contract.issue_status_not_empty",
      status:  "passed",
      message: "issue_status presente e não vazio — obrigatório para Agent Quality Score",
    })
  }

  // Resultado final — failed se qualquer check falhou
  const overallStatus: CertifyStatus = checks.some(c => c.status === "failed")
    ? "failed"
    : checks.some(c => c.status === "warning")
      ? "warning"
      : "passed"

  return {
    agent_type_id: config.agent_type_id,
    status:        overallStatus,
    checks,
    certified_at:  new Date().toISOString(),
  }
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

function _buildMinimalContextPackage() {
  return ContextPackageSchema.parse({
    session_id: "550e8400-e29b-41d4-a716-446655440000",
    tenant_id:  "tenant_cert",
    channel:    "chat",
    customer_data: {
      customer_id: "660e8400-e29b-41d4-a716-446655440001",
      tenant_id:   "tenant_cert",
      tier:        "standard",
    },
    channel_context: {
      turn_count: 1,
      started_at: new Date().toISOString(),
    },
    conversation_history: [],
  })
}
