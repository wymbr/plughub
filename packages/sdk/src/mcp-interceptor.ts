/**
 * mcp-interceptor.ts
 * McpInterceptor — interceptor em-processo para agentes nativos (usam @plughub/sdk).
 * Spec: PlugHub seção 9 — MCP interception / hybrid proxy model.
 *
 * Função: envolve TODAS as chamadas a domain MCP Servers (mcp-server-crm, etc.)
 * feitas pelo agente, ANTES de chegarem ao servidor destino:
 *
 *   1. Validação de permissões[] do JWT — local, sem rede (~0ms)
 *   2. Injection guard — heurística regex contra injeção de prompt
 *   3. Encaminhamento para o delegate (chamada MCP real)
 *   4. Escrita de AuditRecord no Kafka — async, fire-and-forget (~0ms)
 *
 * Overhead total por chamada: < 1ms (validação local + escrita não-bloqueante).
 * Zero network hop para validação.
 *
 * Exemplo de uso:
 *   const interceptor = new McpInterceptor({
 *     getSessionToken: () => lifecycle.currentToken,
 *     delegate: (server, tool, args) => mcpClient.callTool(server, tool, args),
 *     kafka_brokers: ["localhost:9092"],
 *   })
 *   interceptor.start()
 *
 *   // No handler do agente:
 *   const result = await interceptor.callTool("mcp-server-crm", "customer_get", { customer_id })
 *
 * Invariante: nenhuma chamada a domain MCP Server pode escapar deste interceptor.
 * Agentes nativos DEVEM usar McpInterceptor em vez de chamar MCP servers diretamente.
 */

import type { AuditRecord, AuditPolicy, AuditContext } from "@plughub/schemas"
import { AuditKafkaWriter, type AuditKafkaConfig }     from "./infra/audit-kafka"

// ─────────────────────────────────────────────
// Injection guard (inline — sync with mcp-server-plughub/src/infra/injection_guard.ts)
// TODO item 4: Extrair para @plughub/schemas e compartilhar entre pacotes
// ─────────────────────────────────────────────

interface InjectionPattern {
  id:       string
  regex:    RegExp
  severity: "low" | "medium" | "high"
}

const INJECTION_PATTERNS: InjectionPattern[] = [
  { id: "override_instructions",   regex: /ignore\s+(previous|all|prior|above)\s+(instructions?|directives?|commands?|prompts?)/i,  severity: "high"   },
  { id: "role_hijack",             regex: /you\s+are\s+now\s+(a|an|acting\s+as|playing|assuming\s+the\s+role)/i,                    severity: "high"   },
  { id: "forget_previous",         regex: /forget\s+(your|all|previous|everything|the\s+above)/i,                                   severity: "high"   },
  { id: "new_instructions_header", regex: /\bnew\s+(instructions?|directives?|task|objective)\s*:/i,                                severity: "high"   },
  { id: "disregard_pattern",       regex: /disregard\s+(previous|your|all|the\s+above|instructions?|rules?)/i,                      severity: "high"   },
  { id: "pretend_persona",         regex: /pretend\s+(you\s+are|to\s+be|that\s+you\s+are)/i,                                        severity: "medium" },
  { id: "act_as_persona",          regex: /act\s+as\s+(if\s+you\s+are|though\s+you\s+are|a\s+different|an?\s+)/i,                  severity: "medium" },
  { id: "system_prompt_leak",      regex: /\bsystem\s+prompt\b|\bsystem\s+message\b/i,                                              severity: "medium" },
  { id: "override_behavior",       regex: /override\s+(your\s+)?(instructions?|behavior|responses?|safety|restrictions?)/i,         severity: "high"   },
  { id: "injection_keyword",       regex: /\bprompt\s+injection\b|\bjailbreak\b|\bdan\s+mode\b/i,                                   severity: "high"   },
  { id: "developer_mode",          regex: /developer\s+mode\s+(enabled|on|activated)/i,                                             severity: "medium" },
  { id: "simulate_unrestricted",   regex: /simulate\s+(being\s+)?(an?\s+)?(unrestricted|unfiltered|uncensored|jailbroken)/i,        severity: "high"   },
  { id: "do_anything_now",         regex: /do\s+anything\s+now|DAN\b/,                                                              severity: "high"   },
]

type InjectionResult =
  | { detected: false }
  | { detected: true; pattern_id: string; severity: "low" | "medium" | "high"; matched: string }

function _stringify(value: unknown, depth = 0): string {
  if (depth > 8) return ""
  if (typeof value === "string")  return value
  if (typeof value === "number" || typeof value === "boolean") return String(value)
  if (Array.isArray(value)) return value.map(v => _stringify(v, depth + 1)).join(" ")
  if (value !== null && typeof value === "object") {
    const parts: string[] = []
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      parts.push(k, _stringify(v, depth + 1))
    }
    return parts.join(" ")
  }
  return ""
}

function detectInjection(input: unknown): InjectionResult {
  const haystack = _stringify(input)
  for (const p of INJECTION_PATTERNS) {
    const m = p.regex.exec(haystack)
    if (m) return { detected: true, pattern_id: p.id, severity: p.severity, matched: m[0] }
  }
  return { detected: false }
}

// ─────────────────────────────────────────────
// JWT helpers (local decode — no signature verification needed for permissions)
// Full verification happens at mcp-server-plughub / Agent Registry.
// ─────────────────────────────────────────────

function _jwtDecode(token: string): Record<string, unknown> {
  try {
    const parts = token.split(".")
    if (parts.length < 2) return {}
    return JSON.parse(Buffer.from(parts[1]!, "base64url").toString("utf-8")) as Record<string, unknown>
  } catch {
    return {}
  }
}

function _extractPermissions(token: string): string[] {
  const p = _jwtDecode(token)["permissions"] ?? _jwtDecode(token)["perms"] ?? []
  return Array.isArray(p) ? (p as string[]) : []
}

function _extractClaims(token: string): { tenant_id: string; instance_id: string; session_id: string } {
  const p = _jwtDecode(token)
  return {
    tenant_id:   String(p["tenant_id"]  ?? "unknown"),
    instance_id: String(p["instance_id"] ?? "unknown"),
    session_id:  String(p["sub"]         ?? "unknown"),
  }
}

/**
 * Verifica se serverName está coberto pelas permissões.
 * Formato de permissão: "mcp-server-crm:customer_get" ou "mcp-server-crm:*"
 */
function _isPermitted(permissions: string[], serverName: string, toolName: string): boolean {
  if (permissions.length === 0) return true  // sem filtro (backward-compatible)
  return permissions.some(p => {
    const [srv, tool] = p.split(":")
    return srv === serverName && (tool === "*" || tool === toolName)
  })
}

// ─────────────────────────────────────────────
// Tipos públicos
// ─────────────────────────────────────────────

/**
 * Delegate que executa a chamada MCP real ao domain server.
 * Injetado pelo agente — McpInterceptor não depende de um cliente MCP específico.
 */
export type McpDelegate = (
  serverName: string,
  toolName:   string,
  args:       unknown,
) => Promise<unknown>

export interface McpInterceptorConfig {
  /**
   * Retorna o session_token atual (chamado a cada callTool para freshness).
   * Tipicamente: () => lifecycle.currentToken
   */
  getSessionToken: () => string

  /** Função que executa a chamada MCP real ao domain server */
  delegate: McpDelegate

  /** Brokers Kafka para escrita de AuditRecords */
  kafka_brokers: string[]

  /** Tópico Kafka para AuditRecords (default: "mcp.audit") */
  audit_topic?: string

  /** Intervalo de flush dos audit records (ms, default: 500) */
  audit_flush_interval_ms?: number
}

export interface CallOptions {
  /** Política de auditoria da tool — usada para capturar input/output no registro */
  audit_policy?: AuditPolicy
  /** Enriquecimento opcional por chamada */
  audit_context?: AuditContext
}

export interface McpInterceptorError extends Error {
  code: "PERMISSION_DENIED" | "INJECTION_DETECTED"
  server_name: string
  tool_name:   string
}

// ─────────────────────────────────────────────
// McpInterceptor
// ─────────────────────────────────────────────

export class McpInterceptor {
  private readonly getSessionToken: () => string
  private readonly delegate:        McpDelegate
  private readonly writer:          AuditKafkaWriter

  constructor(cfg: McpInterceptorConfig) {
    this.getSessionToken = cfg.getSessionToken
    this.delegate        = cfg.delegate
    this.writer          = new AuditKafkaWriter({
      brokers:            cfg.kafka_brokers,
      topic:              cfg.audit_topic ?? "mcp.audit",
      flush_interval_ms:  cfg.audit_flush_interval_ms,
    })
  }

  /**
   * Inicia o writer Kafka em background.
   * Deve ser chamado uma vez após instanciar o interceptor.
   */
  start(): void {
    this.writer.start()
  }

  /**
   * Para o writer Kafka e faz flush final.
   * Chamar no shutdown do agente.
   */
  async stop(): Promise<void> {
    await this.writer.stop()
  }

  /**
   * Intercepts and forwards a tool call to a domain MCP Server.
   *
   * @param serverName — ex: "mcp-server-crm"
   * @param toolName   — ex: "customer_get"
   * @param args       — input do tool (validado contra injection patterns)
   * @param opts       — audit_policy e audit_context opcionais
   *
   * @throws McpInterceptorError(PERMISSION_DENIED) — tool não coberta pelas permissões do JWT
   * @throws McpInterceptorError(INJECTION_DETECTED) — padrão de injeção detectado no input
   */
  async callTool(
    serverName: string,
    toolName:   string,
    args:       unknown,
    opts:       CallOptions = {},
  ): Promise<unknown> {
    const token       = this.getSessionToken()
    const permissions = _extractPermissions(token)
    const claims      = _extractClaims(token)
    const startedAt   = Date.now()

    // ── 1. Validação de permissões ──────────────────────────────────────────
    const permitted = _isPermitted(permissions, serverName, toolName)

    if (!permitted) {
      this._audit({
        claims, serverName, toolName, permissions,
        allowed:            false,
        injection_detected: false,
        duration_ms:        Date.now() - startedAt,
        opts,
        input_snapshot:     undefined,
        output_snapshot:    undefined,
      })
      const err = Object.assign(
        new Error(`[McpInterceptor] Permission denied: '${serverName}:${toolName}' not in JWT permissions`),
        { code: "PERMISSION_DENIED" as const, server_name: serverName, tool_name: toolName }
      )
      throw err
    }

    // ── 2. Injection guard ──────────────────────────────────────────────────
    const injection = detectInjection(args)

    if (injection.detected) {
      this._audit({
        claims, serverName, toolName, permissions,
        allowed:            false,
        injection_detected: true,
        injection_pattern:  injection.pattern_id,
        duration_ms:        Date.now() - startedAt,
        opts,
        input_snapshot:     undefined,
        output_snapshot:    undefined,
      })
      const err = Object.assign(
        new Error(
          `[McpInterceptor] Injection detected in '${serverName}:${toolName}' ` +
          `(pattern: ${injection.pattern_id}, matched: "${injection.matched}")`
        ),
        { code: "INJECTION_DETECTED" as const, server_name: serverName, tool_name: toolName }
      )
      throw err
    }

    // ── 3. Encaminhar para o delegate ───────────────────────────────────────
    let result: unknown
    let callError: unknown

    try {
      result = await this.delegate(serverName, toolName, args)
    } catch (e) {
      callError = e
    }

    const duration = Date.now() - startedAt

    // ── 4. Audit record (fire-and-forget) ───────────────────────────────────
    this._audit({
      claims, serverName, toolName, permissions,
      allowed:            true,
      injection_detected: false,
      duration_ms:        duration,
      opts,
      input_snapshot:     opts.audit_policy?.capture_input  ? args   : undefined,
      output_snapshot:    opts.audit_policy?.capture_output ? result : undefined,
    })

    if (callError !== undefined) throw callError
    return result
  }

  // ─── Private ─────────────────────────────────────────────────────────────

  private _audit(params: {
    claims:             { tenant_id: string; instance_id: string; session_id: string }
    serverName:         string
    toolName:           string
    permissions:        string[]
    allowed:            boolean
    injection_detected: boolean
    injection_pattern?: string
    duration_ms:        number
    opts:               CallOptions
    input_snapshot:     unknown
    output_snapshot:    unknown
  }): void {
    const record: AuditRecord = {
      event_type:          "mcp.tool_call",
      timestamp:           new Date().toISOString(),
      tenant_id:           params.claims.tenant_id,
      session_id:          params.claims.session_id,
      instance_id:         params.claims.instance_id,
      server_name:         params.serverName,
      tool_name:           params.toolName,
      allowed:             params.allowed,
      permissions_checked: params.permissions,
      injection_detected:  params.injection_detected,
      injection_pattern:   params.injection_pattern,
      duration_ms:         params.duration_ms,
      data_categories:     params.opts.audit_policy?.data_categories,
      input_snapshot:      params.input_snapshot,
      output_snapshot:     params.output_snapshot,
      audit_context:       params.opts.audit_context,
      source:              "in_process",
    }
    this.writer.write(record)
  }
}
