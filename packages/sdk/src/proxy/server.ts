/**
 * proxy/server.ts
 * HTTP proxy sidecar for external agents.
 * Spec: PlugHub v24.0 section 4.6k
 *
 * Per MCP call:
 *   1. Decode JWT permissions[] locally — ~0.1ms, zero network
 *   2. Forward to domain MCP Server via routes[]
 *   3. Write audit event to in-memory buffer (async, ~0ms)
 *   4. Background timer drains buffer (logs in MVP; real impl → Kafka)
 */

import * as http from "node:http"
import type { ProxyConfig } from "./config"
import { CircuitBreaker, CircuitBreakerError } from "./circuit-breaker"

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

export interface AuditEvent {
  timestamp:    string
  session_id:   string
  server:       string
  path:         string
  allowed:      boolean
  duration_ms:  number
}

export interface ProxySidecar {
  start(): Promise<void>
  stop(): Promise<void>
  /** Exposed for testing — number of events currently buffered. */
  get auditBufferSize(): number
}

// ─────────────────────────────────────────────
// JWT permission extraction (local, no network)
// ─────────────────────────────────────────────

function extractJwtPermissions(token: string): string[] {
  try {
    const parts   = token.split(".")
    if (parts.length < 2) return []
    const payload = JSON.parse(Buffer.from(parts[1]!, "base64url").toString("utf-8")) as Record<string, unknown>
    const perms   = payload["permissions"] ?? payload["perms"] ?? []
    return Array.isArray(perms) ? (perms as string[]) : []
  } catch {
    return []
  }
}

function extractSessionId(token: string): string {
  try {
    const parts   = token.split(".")
    if (parts.length < 2) return "unknown"
    const payload = JSON.parse(Buffer.from(parts[1]!, "base64url").toString("utf-8")) as Record<string, unknown>
    return String(payload["session_id"] ?? payload["sub"] ?? "unknown")
  } catch {
    return "unknown"
  }
}

/**
 * Validates that the requested server is covered by the JWT permissions.
 * Permissions format: "mcp-server-crm:customer_get"
 * Validation: any permission targeting the requested server is sufficient.
 */
function isServerAllowed(permissions: string[], serverName: string): boolean {
  return permissions.some(p => p.startsWith(`${serverName}:`))
}

// ─────────────────────────────────────────────
// Proxy server factory
// ─────────────────────────────────────────────

export function createProxySidecar(config: ProxyConfig, sessionToken: string): ProxySidecar {
  const breaker     = new CircuitBreaker(config.circuit_breaker.timeout_ms)
  const auditBuffer: AuditEvent[] = []
  let   server:      http.Server | null = null
  let   flushTimer:  ReturnType<typeof setInterval> | null = null

  // Background timer — drains audit buffer (logs to stdout in MVP; real impl → Kafka)
  function startFlushTimer(): void {
    flushTimer = setInterval(() => {
      const events = auditBuffer.splice(0, config.audit_buffer_size)
      if (events.length > 0) {
        for (const e of events) {
          process.stdout.write(
            `[audit] ${e.timestamp} session=${e.session_id} server=${e.server} ` +
            `path=${e.path} allowed=${e.allowed} duration=${e.duration_ms}ms\n`
          )
        }
      }
    }, config.audit_flush_interval_ms)
    if (typeof flushTimer.unref === "function") flushTimer.unref()
  }

  function addAuditEvent(event: AuditEvent): void {
    if (auditBuffer.length < config.audit_buffer_size) {
      auditBuffer.push(event)
    }
  }

  const requestHandler: http.RequestListener = (req, res) => {
    // Path: /{server-name}/{rest}  →  forward to routes[server-name]/{rest}
    const urlPath   = req.url ?? "/"
    const pathParts = urlPath.replace(/^\//, "").split("/")
    const serverKey = pathParts[0] ?? ""
    const upstreamBase = config.routes[serverKey]
    const started   = Date.now()

    const token       = sessionToken
    const permissions = extractJwtPermissions(token)
    const sessionId   = extractSessionId(token)
    const allowed     = isServerAllowed(permissions, serverKey)

    if (!allowed || !upstreamBase) {
      const reason = !upstreamBase
        ? `unknown server '${serverKey}' — not in routes`
        : `permission denied for server '${serverKey}'`

      addAuditEvent({
        timestamp:   new Date().toISOString(),
        session_id:  sessionId,
        server:      serverKey,
        path:        urlPath,
        allowed:     false,
        duration_ms: Date.now() - started,
      })

      res.writeHead(403, { "Content-Type": "application/json" })
      res.end(JSON.stringify({ error: "permission_denied", detail: reason }))
      return
    }

    // Collect request body
    const bodyChunks: Buffer[] = []
    req.on("data", (chunk: Buffer) => bodyChunks.push(chunk))
    req.on("end", () => {
      const body      = Buffer.concat(bodyChunks)
      const restPath  = "/" + pathParts.slice(1).join("/")
      const targetUrl = new URL(restPath, upstreamBase.endsWith("/") ? upstreamBase : upstreamBase + "/")

      breaker
        .execute(() => forwardRequest(req, body, targetUrl))
        .then(({ statusCode, headers, responseBody }) => {
          addAuditEvent({
            timestamp:   new Date().toISOString(),
            session_id:  sessionId,
            server:      serverKey,
            path:        urlPath,
            allowed:     true,
            duration_ms: Date.now() - started,
          })
          res.writeHead(statusCode, headers)
          res.end(responseBody)
        })
        .catch((err: unknown) => {
          addAuditEvent({
            timestamp:   new Date().toISOString(),
            session_id:  sessionId,
            server:      serverKey,
            path:        urlPath,
            allowed:     true,
            duration_ms: Date.now() - started,
          })
          const body = err instanceof CircuitBreakerError
            ? err.toErrorResponse()
            : { error: "proxy_error", detail: String(err) }
          res.writeHead(502, { "Content-Type": "application/json" })
          res.end(JSON.stringify(body))
        })
    })
  }

  return {
    async start(): Promise<void> {
      return new Promise((resolve, reject) => {
        server = http.createServer(requestHandler)
        server.listen(config.port, "127.0.0.1", () => {
          startFlushTimer()
          process.stdout.write(
            `[plughub-sdk proxy] listening on localhost:${config.port}\n` +
            `[plughub-sdk proxy] routes: ${Object.keys(config.routes).join(", ")}\n`
          )
          resolve()
        })
        server.on("error", reject)
      })
    },

    async stop(): Promise<void> {
      if (flushTimer) { clearInterval(flushTimer); flushTimer = null }
      return new Promise((resolve) => {
        if (!server) { resolve(); return }
        server.close(() => { server = null; resolve() })
      })
    },

    get auditBufferSize(): number {
      return auditBuffer.length
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
      headers:  {
        ...incomingReq.headers,
        host: targetUrl.host,
      },
    }

    const proxyReq = httpMod.request(options, (proxyRes) => {
      const chunks: Buffer[] = []
      proxyRes.on("data", (c: Buffer) => chunks.push(c))
      proxyRes.on("end", () => {
        resolve({
          statusCode:   proxyRes.statusCode ?? 200,
          headers:      proxyRes.headers as http.OutgoingHttpHeaders,
          responseBody: Buffer.concat(chunks),
        })
      })
    })

    proxyReq.on("error", reject)
    if (body.length > 0) proxyReq.write(body)
    proxyReq.end()
  })
}
