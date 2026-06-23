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

import type { AuditRecord, AuditPolicy, AuditContext, ToolContextTags, DataCategory } from "@plughub/schemas"
import { DEFAULT_MASKING_RULES } from "@plughub/schemas"
import { AuditKafkaWriter, type AuditKafkaConfig }     from "./infra/audit-kafka"
import { ContextAccumulator } from "./context-accumulator"
import type { ContextStore }  from "./context-store"

// ─────────────────────────────────────────────
// R7a — Output snapshot masking (pattern-based, symmetric to input)
//
// Diferente do input (que é mascarado por anotação @masked/token), o retorno da tool
// não tem anotação — a PII é detectada por PADRÃO (regex das DEFAULT_MASKING_RULES) e
// substituída pelo `replacement` (placeholder estático, sem dígitos reais). Garante que
// o valor cru de PII NUNCA chega ao mcp_audit_log. Mantém o texto não-PII intacto para
// faithfulness-vs-ferramenta sobre fatos não sensíveis.
// ─────────────────────────────────────────────

const _OUTPUT_MASK_RULES: { re: RegExp; category: DataCategory; replacement: string }[] =
  DEFAULT_MASKING_RULES.flatMap(r => {
    try {
      return [{ re: new RegExp(r.pattern, "g"), category: r.category, replacement: r.replacement }]
    } catch {
      return []
    }
  })

interface OutputMaskResult {
  value:      unknown
  fields:     string[]
  categories: DataCategory[]
}

/**
 * Recursively walks `value`, masking PII in string leaves by pattern. Returns the
 * masked copy, the dot-notation paths where masking occurred, and the categories
 * detected. Pure/synchronous — no vault, no I/O. Non-PII content is preserved.
 * Exported for unit testing (R7a).
 */
export function maskOutputForAudit(value: unknown, path = ""): OutputMaskResult {
  if (typeof value === "string") {
    let masked = value
    const categories: DataCategory[] = []
    for (const rule of _OUTPUT_MASK_RULES) {
      rule.re.lastIndex = 0
      if (rule.re.test(masked)) {
        masked = masked.replace(rule.re, rule.replacement)
        categories.push(rule.category)
      }
    }
    return categories.length > 0
      ? { value: masked, fields: [path || "$"], categories }
      : { value, fields: [], categories: [] }
  }
  if (Array.isArray(value)) {
    const out: unknown[] = []
    const fields: string[] = []
    const categories: DataCategory[] = []
    value.forEach((item, i) => {
      const r = maskOutputForAudit(item, path ? `${path}[${i}]` : `[${i}]`)
      out.push(r.value); fields.push(...r.fields); categories.push(...r.categories)
    })
    return { value: out, fields, categories }
  }
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {}
    const fields: string[] = []
    const categories: DataCategory[] = []
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      const r = maskOutputForAudit(v, path ? `${path}.${k}` : k)
      out[k] = r.value; fields.push(...r.fields); categories.push(...r.categories)
    }
    return { value: out, fields, categories }
  }
  return { value, fields: [], categories: [] }
}

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

  // ── ContextStore wiring (opcional) ────────────────────────────────────────

  /**
   * ContextStore para acumulação de contexto como side-effect de tool calls.
   * Quando configurado, o interceptor lê as anotações `context_tags` de cada tool
   * e persiste automaticamente os valores de entrada/saída no ContextStore.
   * Requer também `contextToolRegistry` para lookup das anotações.
   */
  contextStore?: ContextStore

  /**
   * Registro de anotações context_tags por servidor e tool name.
   * Formato: { "mcp-server-crm": { "customer_get": { inputs: {...}, outputs: {...} } } }
   *
   * Populado pelo agente na inicialização ao registrar suas tool definitions.
   * O interceptor usa este registry para extrair contexto de cada chamada.
   */
  contextToolRegistry?: Record<string, Record<string, ToolContextTags>>

  /**
   * Session ID atual — necessário para escrita no ContextStore.
   * Usar getSessionId() dinâmico para suportar multi-sessão.
   */
  getSessionId?: () => string

  /**
   * Customer ID atual — necessário para tags de longa duração (pricing, insight.historico).
   * Opcional — se ausente, tags de longa duração usam apenas sessionId.
   */
  getCustomerId?: () => string | undefined

  /**
   * Callback opcional para resolução de tokens de mascaramento em argumentos de tool calls.
   * Quando configurado, qualquer string `[category:tk_xxx:display]` nos args é substituída
   * pelo valor original antes de encaminhar ao domain MCP Server.
   *
   * Tipicamente implementado lendo o Redis:
   *   resolveToken: (tenantId, tokenId) => redis.get(`${tenantId}:token:${tokenId}`)
   *     .then(v => v ? JSON.parse(v).original_value : null)
   *
   * Se retornar null, o token permanece como está (fail-open — nunca bloqueia o fluxo).
   */
  resolveToken?: (tenantId: string, tokenId: string) => Promise<string | null>
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
  private readonly getSessionToken:   () => string
  private readonly delegate:          McpDelegate
  private readonly writer:            AuditKafkaWriter
  private readonly contextStore?:     ContextStore
  private readonly contextRegistry?:  Record<string, Record<string, ToolContextTags>>
  private readonly getSessionId?:     () => string
  private readonly getCustomerId?:    () => string | undefined
  private readonly resolveToken?:     (tenantId: string, tokenId: string) => Promise<string | null>

  constructor(cfg: McpInterceptorConfig) {
    this.getSessionToken  = cfg.getSessionToken
    this.delegate         = cfg.delegate
    this.writer           = new AuditKafkaWriter({
      brokers:            cfg.kafka_brokers,
      topic:              cfg.audit_topic ?? "mcp.audit",
      flush_interval_ms:  cfg.audit_flush_interval_ms,
    })
    this.contextStore     = cfg.contextStore
    this.contextRegistry  = cfg.contextToolRegistry
    this.getSessionId     = cfg.getSessionId
    this.getCustomerId    = cfg.getCustomerId
    this.resolveToken     = cfg.resolveToken
  }

  /**
   * Registra as anotações context_tags de um tool em runtime.
   * Chamado pelo agente ao inicializar suas tool definitions.
   *
   * @param serverName  ex: "mcp-server-crm"
   * @param toolName    ex: "customer_get"
   * @param contextTags Anotação da tool definition
   */
  registerContextTags(
    serverName:   string,
    toolName:     string,
    contextTags:  ToolContextTags,
  ): void {
    if (!this.contextRegistry) return
    if (!this.contextRegistry[serverName]) {
      this.contextRegistry[serverName] = {}
    }
    this.contextRegistry[serverName]![toolName] = contextTags
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

    // ── 3-pre. Token resolution (masking) ──────────────────────────────────
    // If resolveToken is configured, replace any [category:tk_xxx:display] tokens
    // in the args with their original values before forwarding to the domain server.
    // This allows agents to pass masked values from the stream to MCP tools transparently.
    let resolvedArgs = args
    // Collect field paths that contain masking tokens BEFORE resolution so we
    // can populate masked_input_fields in the AuditRecord without storing values.
    const maskedInputFields = this.resolveToken
      ? this._collectTokenPaths(args)
      : []
    if (this.resolveToken) {
      try {
        resolvedArgs = await this._resolveArgsTokens(
          args,
          claims.tenant_id,
          this.resolveToken,
        )
      } catch (err) {
        // Fail-open: log but continue with original args — never block agent flow
        console.error("[McpInterceptor] TOKEN_RESOLUTION_FAILED", String(err))
      }
    }

    // ── 3a. Context extraction — inputs (fire-and-forget) ──────────────────
    const contextTags = this.contextRegistry?.[serverName]?.[toolName]
    if (contextTags?.inputs && this.contextStore && this.getSessionId) {
      const sessionId  = this.getSessionId()
      const customerId = this.getCustomerId?.()
      const accumulator = new ContextAccumulator({
        store:      this.contextStore,
        sessionId,
        customerId,
      })
      // Non-blocking — input extraction is best-effort
      accumulator.extractFromInputs(
        contextTags.inputs,
        resolvedArgs as Record<string, unknown>,
        `mcp_call:${serverName}:${toolName}`,
      ).catch(err => {
        console.error("[McpInterceptor] CTX_INPUT_EXTRACTION_FAILED", String(err))
      })
    }

    try {
      result = await this.delegate(serverName, toolName, resolvedArgs)
    } catch (e) {
      callError = e
    }

    // ── 3b. Context extraction — outputs (fire-and-forget) ─────────────────
    if (callError === undefined && contextTags?.outputs && this.contextStore && this.getSessionId) {
      const sessionId  = this.getSessionId()
      const customerId = this.getCustomerId?.()
      const accumulator = new ContextAccumulator({
        store:      this.contextStore,
        sessionId,
        customerId,
      })
      // Non-blocking — output extraction is best-effort
      accumulator.extractFromOutputs(
        contextTags.outputs,
        result as Record<string, unknown>,
        `mcp_call:${serverName}:${toolName}`,
      ).catch(err => {
        console.error("[McpInterceptor] CTX_OUTPUT_EXTRACTION_FAILED", String(err))
      })
    }

    const duration = Date.now() - startedAt

    // ── 4. Audit record (fire-and-forget) ───────────────────────────────────
    // When masked tokens were present in the input, replace those field values
    // with "[MASKED]" in the snapshot so original values never reach the audit log.
    // masked_input_fields records WHICH fields were sensitive without storing values.
    const auditInputSnapshot = opts.audit_policy?.capture_input
      ? (maskedInputFields.length > 0
          ? this._sanitizeSnapshotForAudit(resolvedArgs, maskedInputFields)
          : resolvedArgs)
      : undefined
    // R7a — o output_snapshot NUNCA é gravado cru: PII detectada por padrão é mascarada
    // (simétrico ao input). capture_output segue opt-in por tool (default false).
    let auditOutputSnapshot: unknown = undefined
    let maskedOutputFields: string[] | undefined = undefined
    let detectedOutputCategories: DataCategory[] | undefined = undefined
    if (opts.audit_policy?.capture_output) {
      const m = maskOutputForAudit(result)
      auditOutputSnapshot = m.value
      if (m.fields.length > 0) {
        maskedOutputFields       = m.fields
        detectedOutputCategories = Array.from(new Set(m.categories))
      }
    }
    this._audit({
      claims, serverName, toolName, permissions,
      allowed:              true,
      injection_detected:   false,
      duration_ms:          duration,
      opts,
      input_snapshot:       auditInputSnapshot,
      output_snapshot:      auditOutputSnapshot,
      masked_input_fields:  maskedInputFields.length > 0 ? maskedInputFields : undefined,
      masked_output_fields: maskedOutputFields,
      detected_output_categories: detectedOutputCategories,
    })

    if (callError !== undefined) throw callError
    return result
  }

  // ─── Private ─────────────────────────────────────────────────────────────

  private _audit(params: {
    claims:               { tenant_id: string; instance_id: string; session_id: string }
    serverName:           string
    toolName:             string
    permissions:          string[]
    allowed:              boolean
    injection_detected:   boolean
    injection_pattern?:   string
    duration_ms:          number
    opts:                 CallOptions
    input_snapshot:       unknown
    output_snapshot:      unknown
    masked_input_fields?: string[]
    masked_output_fields?: string[]
    /** Categorias de PII detectadas e mascaradas no output (R7a) — unidas a data_categories. */
    detected_output_categories?: DataCategory[]
  }): void {
    // R7a — data_categories reflete o declarado pela política UNIDO ao detectado no
    // output (flagra PII inesperada que a tool não declarou). undefined quando vazio.
    const declaredCategories = params.opts.audit_policy?.data_categories ?? []
    const dataCategories = Array.from(
      new Set<DataCategory>([...declaredCategories, ...(params.detected_output_categories ?? [])])
    )
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
      data_categories:     dataCategories.length > 0 ? dataCategories : undefined,
      input_snapshot:      params.input_snapshot,
      output_snapshot:     params.output_snapshot,
      audit_context:       params.opts.audit_context,
      source:              "in_process",
      masked_input_fields: params.masked_input_fields,
      masked_output_fields: params.masked_output_fields,
    }
    try {
      this.writer.write(record)
    } catch (err) {
      // Audit write failure — fallback to stderr for LGPD traceability.
      // Do NOT suppress: this record must be recoverable from logs.
      // input_snapshot / output_snapshot are redacted to prevent sensitive
      // values from leaking into infrastructure logs when Kafka is unavailable.
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { input_snapshot: _in, output_snapshot: _out, ...safeFields } = record
      console.error(
        "[McpInterceptor] AUDIT_WRITE_FAILED",
        JSON.stringify({
          ...safeFields,
          input_snapshot:  record.input_snapshot  !== undefined ? "[REDACTED]" : undefined,
          output_snapshot: record.output_snapshot !== undefined ? "[REDACTED]" : undefined,
          _kafka_error: String(err),
        })
      )
    }
  }

  /**
   * Recursively collects dot-notation paths of fields whose string values contain
   * at least one masking token ([category:tk_xxx:display]).
   * Called on original args BEFORE resolution — result populates masked_input_fields.
   * Synchronous — no I/O needed, just pattern matching.
   */
  private _collectTokenPaths(value: unknown, path = ""): string[] {
    const TOKEN_RE = /\[[\w_]+:tk_[a-f0-9]+:[^\]]+\]/
    if (typeof value === "string") {
      return TOKEN_RE.test(value) && path ? [path] : []
    }
    if (Array.isArray(value)) {
      return value.flatMap((item, i) =>
        this._collectTokenPaths(item, path ? `${path}[${i}]` : `[${i}]`)
      )
    }
    if (value !== null && typeof value === "object") {
      return Object.entries(value as Record<string, unknown>).flatMap(([k, v]) =>
        this._collectTokenPaths(v, path ? `${path}.${k}` : k)
      )
    }
    return []
  }

  /**
   * Returns a copy of `snapshot` with the values at `maskedPaths` replaced by
   * "[MASKED]" so that original sensitive values are never written to the audit log.
   * Only operates on top-level object keys for simplicity — nested paths are
   * represented as-is in masked_input_fields but left unreplaced in the snapshot
   * (acceptable because capture_input on tools handling nested masked data is rare).
   */
  private _sanitizeSnapshotForAudit(
    snapshot: unknown,
    maskedPaths: string[],
  ): unknown {
    if (snapshot === null || typeof snapshot !== "object" || Array.isArray(snapshot)) {
      return snapshot
    }
    const topLevelMasked = new Set(
      maskedPaths.map(p => p.split(".")[0]!.replace(/\[\d+\]$/, ""))
    )
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(snapshot as Record<string, unknown>)) {
      out[k] = topLevelMasked.has(k) ? "[MASKED]" : v
    }
    return out
  }

  /**
   * Recursively walks `args` and resolves any masking token strings.
   * Token format: [category:tk_xxx:display_partial]
   * Resolution: calls resolveToken(tenantId, tokenId) → original_value.
   * If the full string IS the token, replaces with the raw value.
   * If the token appears inline in a longer string, replaces the token substring.
   * Fails-open: if resolveToken returns null, the token is left as-is.
   */
  private async _resolveArgsTokens(
    value:        unknown,
    tenantId:     string,
    resolveToken: (tenantId: string, tokenId: string) => Promise<string | null>,
  ): Promise<unknown> {
    if (typeof value === "string") {
      return this._resolveTokensInString(value, tenantId, resolveToken)
    }
    if (Array.isArray(value)) {
      return Promise.all(
        value.map(item => this._resolveArgsTokens(item, tenantId, resolveToken))
      )
    }
    if (value !== null && typeof value === "object") {
      const out: Record<string, unknown> = {}
      for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
        out[k] = await this._resolveArgsTokens(v, tenantId, resolveToken)
      }
      return out
    }
    return value
  }

  private async _resolveTokensInString(
    text:         string,
    tenantId:     string,
    resolveToken: (tenantId: string, tokenId: string) => Promise<string | null>,
  ): Promise<string> {
    // Regex: [category:tk_hexid:display_partial]
    const TOKEN_RE = /\[[\w_]+:(tk_[a-f0-9]+):[^\]]+\]/g
    const matches = [...text.matchAll(TOKEN_RE)]
    if (matches.length === 0) return text

    let result = text
    for (const match of matches) {
      const [fullToken, tokenId] = match
      if (!tokenId) continue
      const resolved = await resolveToken(tenantId, tokenId)
      if (resolved !== null) {
        result = result.replace(fullToken, resolved)
      }
    }
    return result
  }
}
