/**
 * lib/invoke-audit.ts
 * Veredicto e registro de auditoria da tool `invoke` (grupo External Agent).
 *
 * Por que existe como módulo próprio (puro, sem Redis/Kafka):
 *   O `invoke` é a TERCEIRA borda de interceptação MCP da plataforma — as outras
 *   duas são o `McpInterceptor` em-processo (agente nativo) e o proxy sidecar
 *   (agente externo que chama o domain server direto). As três aplicam a MESMA
 *   regra — permissão → injection guard → AuditRecord — e é este módulo que
 *   impede a do `invoke` de divergir sem nada ficar vermelho. Mesma postura de
 *   `lib/assignment-filter.ts`: o veredicto é puro e testável; o I/O fica no
 *   call site.
 *
 * Contexto do defeito que originou o módulo (2026-08-13):
 *   `invoke` publicava em `audit.mcp_calls` — tópico sem consumidor, payload
 *   fora do `AuditRecordSchema` — e NÃO passava injection guard. As duas falhas
 *   eram silenciosas: a chamada de domínio funcionava, só não deixava rastro
 *   auditável em lugar nenhum (o consumer do analytics-api lê `mcp.audit`).
 *
 * Spec: PlugHub seção 9 — MCP interception / audit policy.
 */

import type { AuditRecord } from "@plughub/schemas"
import { detectInjection }   from "../infra/injection_guard"

// ─── Veredicto ────────────────────────────────────────────────────────────────

export type InvokeVerdict =
  | { allowed: true }
  | { allowed: false; reason: "permission_denied"; required: string }
  | { allowed: false; reason: "injection_detected"; pattern_id: string; detail: string }

/**
 * Decide se uma chamada de domínio pode ser encaminhada.
 *
 * Ordem dos gates = a do proxy sidecar: permissão ANTES de injection. Uma chamada
 * não autorizada é recusada pelo motivo mais forte, e o conteúdo dos params de
 * quem não tinha permissão nem chega a ser inspecionado.
 *
 * ⚠️ ASSIMETRIA CONHECIDA com o sidecar (`sdk/src/proxy/server.ts:_isPermitted`),
 * mantida de propósito nesta fatia porque só se pode fechá-la ABRINDO acesso:
 *   - aqui:     match exato de `"{server}:{tool}"`; lista vazia ⇒ NEGA tudo.
 *   - sidecar:  aceita curinga `"{server}:*"`; lista vazia ⇒ SEM filtro.
 * Consequência prática: um agent-type registrado com `mcp-server-crm:*` é
 * recusado pelo `invoke` e aceito pelo sidecar. O lado seguro é este; unificar
 * é decisão de produto, não limpeza — e deve ser feita nos dois de uma vez.
 */
export function judgeInvoke(
  permissions: string[],
  mcpServer:   string,
  tool:        string,
  params:      unknown,
): InvokeVerdict {
  const required = `${mcpServer}:${tool}`
  if (!permissions.includes(required)) {
    return { allowed: false, reason: "permission_denied", required }
  }

  const injection = detectInjection(params)
  if (injection.detected) {
    return {
      allowed:    false,
      reason:     "injection_detected",
      pattern_id: injection.pattern_id,
      detail:     `${injection.description} (severity: ${injection.severity})`,
    }
  }

  return { allowed: true }
}

// ─── AuditRecord ──────────────────────────────────────────────────────────────

export interface InvokeAuditInput {
  tenant_id:   string
  /** "" quando não atribuível a uma sessão — NUNCA um placeholder tipo "unknown". */
  session_id:  string
  instance_id: string
  mcp_server:  string
  tool:        string
  permissions: string[]
  verdict:     InvokeVerdict
  duration_ms: number
  timestamp?:  string
}

/**
 * Monta o `AuditRecord` do tópico canônico `mcp.audit`.
 *
 * `session_id` vazio é deliberado e tem consequência conhecida: o consumer do
 * analytics-api (`parse_mcp_audit_event`) descarta o evento como "chamada de
 * sistema, não atribuível a sessão". Preencher com `"unknown"` criaria uma linha
 * em `session_timeline` sob uma sessão que não existe — um valor plausível
 * escondendo a falta de atribuição, que é o que se quer ver.
 */
export function buildInvokeAuditRecord(input: InvokeAuditInput): AuditRecord {
  const { verdict } = input

  const record: AuditRecord = {
    event_type:          "mcp.tool_call",
    timestamp:           input.timestamp ?? new Date().toISOString(),
    tenant_id:           input.tenant_id,
    session_id:          input.session_id,
    instance_id:         input.instance_id,
    server_name:         input.mcp_server,
    tool_name:           input.tool,
    allowed:             verdict.allowed,
    permissions_checked: input.permissions,
    injection_detected:  verdict.allowed === false && verdict.reason === "injection_detected",
    duration_ms:         Math.max(0, Math.round(input.duration_ms)),
    source:              "mcp_server_invoke",
  }

  if (verdict.allowed === false && verdict.reason === "injection_detected") {
    record.injection_pattern = verdict.pattern_id
  }

  return record
}
