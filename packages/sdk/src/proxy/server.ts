/**
 * proxy/server.ts
 * HTTP proxy sidecar para agentes externos (LangGraph, CrewAI, etc.).
 * Spec: PlugHub seção 9 — MCP interception / hybrid proxy model (proxy sidecar).
 *
 * Por chamada MCP (JSON-RPC sobre HTTP):
 *   1. Extrai session_token do environment (configurado pelo Routing Engine)
 *   2. Decodifica JWT localmente — valida permissions[] (~0.1ms, sem rede)
 *   3. Valida se server + tool está coberto pelas permissões
 *   4. Aplica injection guard no body da tool call
 *   5. Encaminha para o domain MCP Server via routes[]
 *   6. Escreve AuditRecord no Kafka assincronamente (fire-and-forget, ~0ms)
 *
 * Overhead por chamada: < 1ms (validação local + escrita não-bloqueante).
 *
 * Path de roteamento: /{server-name}/{rest} → routes[server-name]/{rest}
 * Ex: POST /mcp-server-crm/mcp → http://crm-service:3500/mcp
 */

import * as http             from "node:http"
import type { ProxyConfig }  from "./config"
import { CircuitBreaker, CircuitBreakerError } from "./circuit-breaker"
import { AuditKafkaWriter }  from "../infra/audit-kafka"
import type { AuditRecord }  from "@plughub/schemas"

// ─────────────────────────────────────────────
// Injection guard (inline — sync com mcp-server-plughub/src/infra/injection_guard.ts)
// TODO item 4: Extrair para @plughub/schemas e compartilhar entre pacotes
// ─────────────────────────────────────────────

const _INJECTION_PATTERNS: Array<{ id: string; regex: RegExp }> = [
  { id: "override_instructions",   regex: /ignore\s+(previous|all|prior|above)\s+(instructions?|directives?|commands?|prompts?)/i },
  { id: "role_hijack",             regex: /you\s+are\s+now\s+(a|an|acting\s+as|playing|assuming\s+the\s+role)/i },
  { id: "forget_previous",         regex: /forget\s+(your|all|previous|everything|the\s+above)/i },
  { id: "new_instructions_header", regex: /\bnew\s+(instructions?|directives?|task|objective)\s*:/i },
  { id: "disregard_pattern",       regex: /disregard\s+(previous|your|all|the\s+above|instructions?|rules?)/i },
  { id: "pretend_persona",         regex: /pretend\s+(you\s+are|to\s+be|that\s+you\s+are)/i },
  { id: "act_as_persona",          regex: /act\s+as\s+(if\s+you\s+are|though\s+you\s+are|a\s+different|an?\s+)/i },
  { id: "system_prompt_leak",      regex: /\bsystem\s+prompt\b|\bsystem\s+message\b/i },
  { id: "override_behavior",       regex: /override\s+(your\s+)?(instructions?|behavior|responses?|safety|restrictions?)/i },
  { id: "injection_keyword",       regex: /\bprompt\s+injection\b|\bjailbreak\b|\bdan\s+mode\b/i },
  { id: "developer_mode",          regex: /developer\s+mode\s+(enabled|on|activated)/i },
  { id: "simulate_unrestricted",   regex: /simulate\s+(being\s+)?(an?\s+)?(unrestricted|unfiltered|uncensored|jailbroken)/i },
  { id: "do_anything_now",         regex: /do\s+anything\s+now|DAN\b/ },
]

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

function _detectInjection(input: unknown): { detected: boolean; pattern_id?: string } {
  const haystack = _stringify(input)
  for (const p of _INJECTION_PATTERNS) {
    if (p.regex.test(haystack)) return { detected: true, pattern_id: p.id }
  }
  return { detected: false }
}

// ─────────────────────────────────────────────
// JWT helpers (local decode)
// ─────────────────────────────────────────────

function _jwtDecode(token: string): Record<string, unknown> {
  try {
    const parts = token.split(".")
    if (parts.length < 2) return {}
    return JSON.parse(Buffer.from(parts[1]!, "base64url").toString("utf-8")) as Record<string, unknown>
  } catch { return {} }
}

function _extractPermissions(token: string): string[] {
  const p = _jwtDecode(token)["permissions"] ?? _jwtDecode(token)["perms"] ?? []
  return Array.isArray(p) ? (p as string[]) : []
}

function _extractSessionId(token: string): string {
  return String(_jwtDecode(token)["sub"] ?? "unknown")
}

function _extractTenantId(token: string): string {
  return String(_jwtDecode(token)["tenant_id"] ?? "unknown")
}

function _extractInstanceId(token: string): string {
  return String(_jwtDecode(token)["instance_id"] ?? "unknown")
}

/**
 * Verifica se serverName:toolName está coberto pelas permissões.
 * Formatos aceitos: "mcp-server-crm:customer_get" | "mcp-server-crm:*"
 * Lista vazia = sem filtro (backward-compatible).
 */
function _isPermitted(permissions: string[], serverName: string, toolName: string): boolean {
  if (permissions.length === 0) return true
  return permissions.some(p => {
    const [srv, tool] = p.split(":")
    return srv === serverName && (tool === "*" || tool === toolName || tool === undefined)
  })
}

// ─────────────────────────────────────────────
// MCP JSON-RPC body parsing
// ─────────────────────────────────────────────

interface McpToolCall {
  tool_name: string
  arguments: unknown
}

/**
 * Tenta extrair tool_name e arguments de um body JSON-RPC MCP.
 * Retorna null se o body não é uma tool call ou não é parseable.
 */
function _parseMcpToolCall(body: Buffer): McpToolCall | null {
  try {
    const json = JSON.parse(body.toString("utf-8")) as Record<string, unknown>
    if (json["method"] !== "tools/call") return null
    const params = json["params"] as Record<string, unknown> | undefined
    if (!params) return null
    return {
      tool_name: String(params["name"] ?? "unknown"),
      arguments: params["arguments"] ?? {},
    }
  } catch {
    return null
  }
}

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

export { AuditRecord }  // re-export for consumers

export interface ProxySidecar {
  start(): Promise<void>
  stop(): Promise<void>
  /** Exposed for testing — number of events currently buffered. */
  get auditBufferSize(): number
}

// ─────────────────────────────────────────────
// Factory
// ─────────────────────────────────────────────

export function createProxySidecar(config: ProxyConfig, sessionToken: string): ProxySidecar {
  const breaker = new CircuitBreaker(config.circuit_breaker.timeout_ms)

  // Audit writer: Kafka se kafka_brokers configurado; fallback stdout em dev
  const kafkaWriter: AuditKafkaWriter | null = config.kafka_brokers?.length
    ? new AuditKafkaWriter({
        brokers:           config.kafka_brokers,
        topic:             config.audit_topic ?? "mcp.audit",
        flush_interval_ms: config.audit_flush_interval_ms,
        max_buffer_size:   config.audit_buffer_size,
      })
    : null

  // Fallback stdout buffer (usado quando kafkaWriter = null)
  const stdoutBuffer: AuditRecord[] = []
  let   flushTimer:   ReturnType<typeof setInterval> | null = null
  let   server:       http.Server | null = null

  function _writeAudit(record: AuditRecord): void {
    if (kafkaWriter) {
      kafkaWriter.write(record)
    } else {
      if (stdoutBuffer.length < config.audit_buffer_size) {
        stdoutBuffer.push(record)
      }
    }
  }

  function _startStdoutFlush(): void {
    if (kafkaWriter) return   // Kafka handles its own flush
    flushTimer = setInterval(() => {
      const batch = stdoutBuffer.splice(0, config.audit_buffer_size)
      for (const e of batch) {
        process.stdout.write(
          `[audit] ${e.timestamp} session=${e.session_id} server=${e.server_name} ` +
          `tool=${e.tool_name} allowed=${e.allowed} injection=${e.injection_detected} ` +
          `duration=${e.duration_ms}ms\n`
        )
      }
    }, config.audit_flush_interval_ms)
    if (typeof flushTimer.unref === "function") flushTimer.unref()
  }

  // ─── Request handler ────────────────────────

  const requestHandler: http.RequestListener = (req, res) => {
    const urlPath    = req.url ?? "/"
    const pathParts  = urlPath.replace(/^\//, "").split("/")
    const serverKey  = pathParts[0] ?? ""
    const upstreamBase = config.routes[serverKey]
    const startedAt  = Date.now()

    const permissions = _extractPermissions(sessionToken)
    const sessionId   = _extractSessionId(sessionToken)
    const tenantId    = _extractTenantId(sessionToken)
    const instanceId  = _extractInstanceId(sessionToken)

    // Unknown route → 403 before reading body
    if (!upstreamBase) {
      _writeAudit({
        event_type: "mcp.tool_call", timestamp: new Date().toISOString(),
        tenant_id: tenantId, session_id: sessionId, instance_id: instanceId,
        server_name: serverKey, tool_name: "unknown",
        allowed: false, permissions_checked: permissions,
        injection_detected: false,
        duration_ms: Date.now() - startedAt,
        source: "proxy_sidecar",
      })
      res.writeHead(403, { "Content-Type": "application/json" })
      res.end(JSON.stringify({ error: "permission_denied", detail: `unknown server '${serverKey}'` }))
      return
    }

    // Read body before permission + injection checks (needed for tool_name extraction)
    const bodyChunks: Buffer[] = []
    req.on("data", (chunk: Buffer) => bodyChunks.push(chunk))
    req.on("end", () => {
      const body    = Buffer.concat(bodyChunks)
      const toolCall = _parseMcpToolCall(body)
      const toolName = toolCall?.tool_name ?? "unknown"

      // ── Permission check ───────────────────────────────────────────────────
      const permitted = _isPermitted(permissions, serverKey, toolName)

      if (!permitted) {
        _writeAudit({
          event_type: "mcp.tool_call", timestamp: new Date().toISOString(),
          tenant_id: tenantId, session_id: sessionId, instance_id: instanceId,
          server_name: serverKey, tool_name: toolName,
          allowed: false, permissions_checked: permissions,
          injection_detected: false,
          duration_ms: Date.now() - startedAt,
          source: "proxy_sidecar",
        })
        res.writeHead(403, { "Content-Type": "application/json" })
        res.end(JSON.stringify({
          error: "permission_denied",
          detail: `'${serverKey}:${toolName}' not covered by session permissions`,
        }))
        return
      }

      // ── Injection guard ────────────────────────────────────────────────────
      const injection = toolCall ? _detectInjection(toolCall.arguments) : { detected: false }

      if (injection.detected) {
        _writeAudit({
          event_type: "mcp.tool_call", timestamp: new Date().toISOString(),
          tenant_id: tenantId, session_id: sessionId, instance_id: instanceId,
          server_name: serverKey, tool_name: toolName,
          allowed: false, permissions_checked: permissions,
          injection_detected: true, injection_pattern: injection.pattern_id,
          duration_ms: Date.now() - startedAt,
          source: "proxy_sidecar",
        })
        res.writeHead(400, { "Content-Type": "application/json" })
        res.end(JSON.stringify({
          error: "injection_detected",
          detail: `Prompt injection pattern '${injection.pattern_id}' detected in tool arguments`,
        }))
        return
      }

      // ── Forward to upstream ────────────────────────────────────────────────
      const restPath  = "/" + pathParts.slice(1).join("/")
      const targetUrl = new URL(restPath, upstreamBase.endsWith("/") ? upstreamBase : upstreamBase + "/")

      breaker
        .execute(() => forwardRequest(req, body, targetUrl))
        .then(({ statusCode, headers, responseBody }) => {
          _writeAudit({
            event_type: "mcp.tool_call", timestamp: new Date().toISOString(),
            tenant_id: tenantId, session_id: sessionId, instance_id: instanceId,
            server_name: serverKey, tool_name: toolName,
            allowed: true, permissions_checked: permissions,
            injection_detected: false,
            duration_ms: Date.now() - startedAt,
            source: "proxy_sidecar",
          })
          res.writeHead(statusCode, headers)
          res.end(responseBody)
        })
        .catch((err: unknown) => {
          _writeAudit({
            event_type: "mcp.tool_call", timestamp: new Date().toISOString(),
            tenant_id: tenantId, session_id: sessionId, instance_id: instanceId,
            server_name: serverKey, tool_name: toolName,
            allowed: true, permissions_checked: permissions,
            injection_detected: false,
            duration_ms: Date.now() - startedAt,
            source: "proxy_sidecar",
          })
          const errBody = err instanceof CircuitBreakerError
            ? err.toErrorResponse()
            : { error: "proxy_error", detail: String(err) }
          res.writeHead(502, { "Content-Type": "application/json" })
          res.end(JSON.stringify(errBody))
        })
    })
  }

  return {
    async start(): Promise<void> {
      if (kafkaWriter) kafkaWriter.start()
      else             _startStdoutFlush()

      return new Promise((resolve, reject) => {
        server = http.createServer(requestHandler)
        server.listen(config.port, "127.0.0.1", () => {
          const auditBackend = config.kafka_brokers?.length
            ? `kafka (${config.kafka_brokers.join(",")})`
            : "stdout (MVP)"
          process.stdout.write(
            `[plughub-sdk proxy] listening on localhost:${config.port}\n` +
            `[plughub-sdk proxy] routes: ${Object.keys(config.routes).join(", ")}\n` +
            `[plughub-sdk proxy] audit backend: ${auditBackend}\n`
          )
          resolve()
        })
        server.on("error", reject)
      })
    },

    async stop(): Promise<void> {
      if (flushTimer)  { clearInterval(flushTimer); flushTimer = null }
      if (kafkaWriter) { await kafkaWriter.stop() }
      return new Promise((resolve) => {
        if (!server) { resolve(); return }
        server.close(() => { server = null; resolve() })
      })
    },

    get auditBufferSize(): number {
      // For testing: returns the in-memory count (Kafka writer manages its own buffer)
      return stdoutBuffer.length
    },
  }
}

// ─────────────────────────────────────────────
// HTTP forwarding helper
// ─────────────────────────────────────────────

async function forwardRequest(
  incomingReq: http.IncomingMessage,
  body:        Buffer,
  targetUrl:   URL,
): Promise<{ statusCode: number; headers: http.OutgoingHttpHeaders; responseBody: Buffer }> {
  return new Promise((resolve, reject) => {
    const isHttps  = targetUrl.protocol === "https:"
    const httpMod  = isHttps
      ? require("node:https") as typeof import("node:https")
      : require("node:http")  as typeof import("node:http")

    const options: http.RequestOptions = {
      hostname: targetUrl.hostname,
      port:     targetUrl.port || (isHttps ? 443 : 80),
      path:     targetUrl.pathname + targetUrl.search,
      method:   incomingReq.method ?? "GET",
      headers:  { ...incomingReq.headers, host: targetUrl.host },
    }

    const proxyReq = httpMod.request(options, (proxyRes) => {
      const chunks: Buffer[] = []
      proxyRes.on("data", (c: Buffer) => chunks.push(c))
      proxyRes.on("end", () => resolve({
        statusCode:   proxyRes.statusCode ?? 200,
        headers:      proxyRes.headers as http.OutgoingHttpHeaders,
        responseBody: Buffer.concat(chunks),
      }))
    })

    proxyReq.on("error", reject)
    if (body.length > 0) proxyReq.write(body)
    proxyReq.end()
  })
}
